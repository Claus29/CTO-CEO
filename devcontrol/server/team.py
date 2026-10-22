"""Equipo: usuarios y roles, proyectos, tiempo, ausencias, carga de trabajo, daily,
«en qué trabaja cada uno» e incorporación."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone

from . import auth
from .common import (
    clean_date,
    clean_float,
    clean_int,
    clean_text,
    clean_uid,
    log_activity,
    names,
    notify,
    users_with_perm,
    visible_project_ids,
)
from .db import now_iso, parse_iso, seconds_between
from .entities import find_entity
from .permissions import ALL_PERMISSIONS, CATALOG
from .web import HttpError, Response, bad_request, forbidden, not_found

KEY_RE = re.compile(r"^[A-Z][A-Z0-9]{1,7}$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def week_bounds(value: str | None) -> tuple[date, date]:
    d = date.fromisoformat(value) if value else date.today()
    start = d - timedelta(days=d.weekday())
    return start, start + timedelta(days=6)


def register(router, app):
    db = app.db

    def roles_map():
        return {r["id"]: r for r in db.query("SELECT * FROM roles")}

    def admin_count(c, exclude_user: int | None = None) -> int:
        q = ("SELECT COUNT(*) FROM users u JOIN role_permissions rp ON rp.role_id = u.role_id "
             "WHERE rp.perm='sistema.usuarios' AND u.active=1")
        if exclude_user:
            q += f" AND u.id <> {int(exclude_user)}"
        return c.execute(q).fetchone()[0]

    def permissions_changed(c):
        app.perms.invalidate()
        db.emit(c, "permissions")

    # -- usuarios ---------------------------------------------------------------------------
    @router.route("GET", "/api/admin/users", perm="sistema.usuarios")
    def admin_users(req):
        rm = roles_map()
        rows = db.query("SELECT * FROM users ORDER BY active DESC, display_name")
        out = []
        for r in rows:
            u = auth.public_user(r, app.presence, rm)
            u["project_ids"] = [x["project_id"] for x in db.query("SELECT project_id FROM project_members WHERE user_id=?",
                                                                  (r["id"],))]
            u["sessions"] = db.scalar("SELECT COUNT(*) FROM sessions WHERE user_id=? AND expires_at > ?",
                                      (r["id"], now_iso()))
            u["created_at"] = r["created_at"]
            out.append(u)
        return {"items": out}

    def set_projects(c, user_id, project_ids):
        if project_ids is None:
            return
        ids = [clean_int(p, "proyecto") for p in project_ids]
        existing = {r[0] for r in c.execute("SELECT id FROM projects")}
        c.execute("DELETE FROM project_members WHERE user_id=?", (user_id,))
        c.executemany("INSERT OR IGNORE INTO project_members(project_id, user_id) VALUES (?,?)",
                      [(p, user_id) for p in ids if p in existing])

    def member_projects(c, user_id) -> set[int]:
        return {r[0] for r in c.execute("SELECT project_id FROM project_members WHERE user_id=?", (user_id,))}

    def announce_projects(c, me, user_ids, project_ids) -> None:
        """Aviso a quien entra en un proyecto (y empieza a seguirlo)."""
        for pid in project_ids:
            prow = c.execute("SELECT name, key FROM projects WHERE id=?", (pid,)).fetchone()
            if prow:
                notify(db, c, user_ids, "account", f"Te han añadido al proyecto {prow['name']}",
                       f"{me['display_name']} te ha añadido a {prow['key']}: ya ves sus tareas, incidencias y canal.",
                       f"project-added-{pid}-{now_iso()}", "project", pid, exclude=me["id"])

    def role_ok(c, role_id) -> int:
        rid = clean_int(role_id, "rol")
        if not c.execute("SELECT 1 FROM roles WHERE id=?", (rid,)).fetchone():
            raise bad_request("El rol indicado no existe.")
        return rid

    @router.route("POST", "/api/admin/users", perm="sistema.usuarios", idempotent=True)
    def create_user(req):
        data = req.json
        username = clean_text(data.get("username"), "usuario", 40, required=True).lower()
        if not re.match(r"^[a-z0-9_.-]{2,40}$", username):
            raise bad_request("El usuario solo puede tener letras, números, punto, guion y guion bajo (sin espacios).")
        display = clean_text(data.get("display_name"), "nombre visible", 60, required=True)
        auth.validate_new_password(data.get("password"), int(db.get_setting("password_min_length")), username)
        with db.tx() as c:
            if c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                raise bad_request(f"Ya existe el usuario «{username}».")
            rid = role_ok(c, data.get("role_id"))
            cur = c.execute("INSERT INTO users(username, display_name, email, role_id, password_hash, must_change_password, "
                            "capacity_hours, created_at) VALUES (?,?,?,?,?,?,?,?)",
                            (username, display, clean_text(data.get("email"), "correo", 120), rid,
                             auth.hash_password(data["password"]), 1 if data.get("must_change_password", True) else 0,
                             clean_float(data.get("capacity_hours"), "capacidad", 0, 80) or 40, now_iso()))
            uid = cur.lastrowid
            set_projects(c, uid, data.get("project_ids") or [p[0] for p in c.execute("SELECT id FROM projects "
                                                                                       "WHERE archived=0")])
            role = c.execute("SELECT name FROM roles WHERE id=?", (rid,)).fetchone()[0]
            log_activity(db, c, req.user, "user", uid, username, "created",
                         f"Creó el usuario {display} ({role})")
            db.emit(c, "user", "user", uid, actor_id=req.user["id"])
        return Response.json({"id": uid}, 201)

    @router.route("PATCH", "/api/admin/users/{uid}", perm="sistema.usuarios", idempotent=True)
    def edit_user(req, uid):
        data = req.json
        me = req.user
        with db.tx() as c:
            row = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            if not row:
                raise not_found("El usuario no existe.")
            row = dict(row)
            sets, vals, notes = [], [], []
            if "display_name" in data:
                sets.append("display_name=?")
                vals.append(clean_text(data["display_name"], "nombre visible", 60, required=True))
            if "username" in data:
                un = clean_text(data["username"], "usuario", 40, required=True).lower()
                if un != row["username"]:
                    if not re.match(r"^[a-z0-9_.-]{2,40}$", un):
                        raise bad_request("Usuario no válido.")
                    if c.execute("SELECT 1 FROM users WHERE username=? AND id<>?", (un, row["id"])).fetchone():
                        raise bad_request(f"Ya existe el usuario «{un}».")
                    sets.append("username=?")
                    vals.append(un)
                    notes.append(f"usuario «{un}»")
            if "email" in data:
                sets.append("email=?")
                vals.append(clean_text(data["email"], "correo", 120))
            if "capacity_hours" in data:
                sets.append("capacity_hours=?")
                vals.append(clean_float(data["capacity_hours"], "capacidad", 0, 80, optional=False))
            if "role_id" in data:
                rid = role_ok(c, data["role_id"])
                if rid != row["role_id"]:
                    new_perms = {r[0] for r in c.execute("SELECT perm FROM role_permissions WHERE role_id=?", (rid,))}
                    if row["id"] == me["id"] and "sistema.usuarios" not in new_perms:
                        raise forbidden("No puedes quitarte a ti mismo el permiso de gestionar usuarios.")
                    if "sistema.usuarios" not in new_perms and admin_count(c, row["id"]) == 0:
                        raise forbidden("Debe quedar al menos un usuario activo que pueda gestionar usuarios.")
                    sets.append("role_id=?")
                    vals.append(rid)
                    notes.append("rol «" + c.execute("SELECT name FROM roles WHERE id=?", (rid,)).fetchone()[0] + "»")
            if "active" in data:
                active = 1 if data["active"] else 0
                if active != row["active"]:
                    if not active and row["id"] == me["id"]:
                        raise forbidden("No puedes desactivarte a ti mismo.")
                    if not active and admin_count(c, row["id"]) == 0:
                        raise forbidden("No se puede desactivar al último usuario que gestiona usuarios.")
                    sets.append("active=?")
                    vals.append(active)
                    notes.append("activado" if active else "desactivado")
                    if not active:
                        c.execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))
                        app.presence.drop(row["id"])
            if "project_ids" in data:
                before = member_projects(c, row["id"])
                set_projects(c, row["id"], data["project_ids"])
                notes.append("proyectos")
                announce_projects(c, me, [row["id"]], member_projects(c, row["id"]) - before)
            if "role_id=?" in sets:
                rname = c.execute("SELECT name FROM roles WHERE id=?", (vals[sets.index("role_id=?")],)).fetchone()[0]
                notify(db, c, [row["id"]], "account", "Tu rol ha cambiado",
                       f"{me['display_name']} te ha puesto el rol «{rname}». Puede que veas opciones nuevas.",
                       f"role-{row['id']}-{now_iso()}", "user", row["id"], exclude=me["id"])
            if sets:
                c.execute(f"UPDATE users SET {', '.join(sets)} WHERE id=?", vals + [row["id"]])
            log_activity(db, c, me, "user", row["id"], row["username"], "edited",
                         f"Editó el usuario {row['display_name']}" + (f": {', '.join(notes)}" if notes else ""))
            permissions_changed(c)
            db.emit(c, "user", "user", row["id"], actor_id=me["id"])
        return {"ok": True}

    @router.route("POST", "/api/admin/users/{uid}/password", perm="sistema.usuarios")
    def reset_password(req, uid):
        data = req.json
        with db.tx() as c:
            row = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            if not row:
                raise not_found("El usuario no existe.")
            auth.validate_new_password(data.get("new_password"), int(db.get_setting("password_min_length")),
                                       row["username"])
            c.execute("UPDATE users SET password_hash=?, must_change_password=? WHERE id=?",
                      (auth.hash_password(data["new_password"]), 1 if data.get("must_change", True) else 0, row["id"]))
            c.execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))
            log_activity(db, c, req.user, "user", row["id"], row["username"], "password_reset",
                         f"Restableció la contraseña de {row['display_name']}")
        return {"ok": True}

    @router.route("POST", "/api/admin/users/{uid}/logout-all", perm="sistema.usuarios")
    def logout_all(req, uid):
        with db.tx() as c:
            row = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            if not row:
                raise not_found("El usuario no existe.")
            c.execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))
            log_activity(db, c, req.user, "user", row["id"], row["username"], "sessions_closed",
                         f"Cerró todas las sesiones de {row['display_name']}")
        return {"ok": True}

    @router.route("POST", "/api/admin/users/{uid}/reset-2fa", perm="sistema.usuarios")
    def reset_2fa(req, uid):
        with db.tx() as c:
            row = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            if not row:
                raise not_found("El usuario no existe.")
            c.execute("UPDATE users SET totp_enabled=0, totp_secret=NULL, recovery_codes=NULL WHERE id=?", (row["id"],))
            log_activity(db, c, req.user, "user", row["id"], row["username"], "2fa_reset",
                         f"Desactivó la verificación en dos pasos de {row['display_name']}")
        return {"ok": True}

    # -- roles ----------------------------------------------------------------------------------
    @router.route("GET", "/api/admin/roles", perm="sistema.usuarios")
    def roles(req):
        rows = db.query("SELECT * FROM roles ORDER BY locked DESC, id")
        for r in rows:
            r["perms"] = sorted(x["perm"] for x in db.query("SELECT perm FROM role_permissions WHERE role_id=?",
                                                            (r["id"],)))
            r["users"] = db.scalar("SELECT COUNT(*) FROM users WHERE role_id=? AND active=1", (r["id"],))
            tpl = db.query_one("SELECT items FROM onboarding_templates WHERE role_id=?", (r["id"],))
            r["onboarding"] = json.loads(tpl["items"]) if tpl else []
        return {"items": rows, "catalog": [{"key": k, "area": a, "label": label} for k, a, label in CATALOG]}

    def clean_perms(value) -> list[str]:
        if not isinstance(value, list):
            raise bad_request("Lista de permisos no válida.")
        return sorted({p for p in value if p in ALL_PERMISSIONS})

    @router.route("POST", "/api/admin/roles", perm="sistema.usuarios", idempotent=True)
    def new_role(req):
        data = req.json
        name = clean_text(data.get("name"), "nombre del rol", 60, required=True)
        perms = clean_perms(data.get("perms") or [])
        with db.tx() as c:
            if c.execute("SELECT 1 FROM roles WHERE name=? COLLATE NOCASE", (name,)).fetchone():
                raise bad_request(f"Ya existe un rol llamado «{name}».")
            key = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:30] or "rol"
            base = key
            n = 2
            while c.execute("SELECT 1 FROM roles WHERE key=?", (key,)).fetchone():
                key = f"{base}_{n}"
                n += 1
            ts = now_iso()
            cur = c.execute("INSERT INTO roles(key, name, description, locked, created_at, updated_at) VALUES (?,?,?,0,?,?)",
                            (key, name, clean_text(data.get("description"), "descripción", 300), ts, ts))
            c.executemany("INSERT INTO role_permissions(role_id, perm) VALUES (?,?)", [(cur.lastrowid, p) for p in perms])
            c.execute("INSERT INTO onboarding_templates(role_id, items) VALUES (?, ?)",
                      (cur.lastrowid, json.dumps([clean_text(i, "paso", 200) for i in data.get("onboarding") or []
                                                  if str(i).strip()], ensure_ascii=False)))
            log_activity(db, c, req.user, "role", cur.lastrowid, name, "created",
                         f"Creó el rol «{name}» con {len(perms)} permisos")
            permissions_changed(c)
        return Response.json({"id": cur.lastrowid}, 201)

    @router.route("PATCH", "/api/admin/roles/{rid}", perm="sistema.usuarios", idempotent=True)
    def edit_role(req, rid):
        data = req.json
        me = req.user
        with db.tx() as c:
            role = c.execute("SELECT * FROM roles WHERE id=?", (rid,)).fetchone()
            if not role:
                raise not_found("El rol no existe.")
            if role["locked"] and ("perms" in data or "name" in data):
                raise forbidden("El rol de administrador no se puede modificar: siempre tiene todos los permisos.")
            sets, vals = [], []
            if "name" in data:
                sets.append("name=?")
                vals.append(clean_text(data["name"], "nombre", 60, required=True))
            if "description" in data:
                sets.append("description=?")
                vals.append(clean_text(data["description"], "descripción", 300))
            if sets:
                c.execute(f"UPDATE roles SET {', '.join(sets)}, updated_at=? WHERE id=?", vals + [now_iso(), role["id"]])
            if "perms" in data:
                perms = clean_perms(data["perms"])
                if me["role_id"] == role["id"] and "sistema.usuarios" not in perms:
                    raise forbidden("No puedes quitar a tu propio rol el permiso de gestionar usuarios.")
                old = {r[0] for r in c.execute("SELECT perm FROM role_permissions WHERE role_id=?", (role["id"],))}
                c.execute("DELETE FROM role_permissions WHERE role_id=?", (role["id"],))
                c.executemany("INSERT INTO role_permissions(role_id, perm) VALUES (?,?)", [(role["id"], p) for p in perms])
                if admin_count(c) == 0:
                    raise forbidden("Debe quedar al menos un usuario activo que pueda gestionar usuarios.")
                added, removed = sorted(set(perms) - old), sorted(old - set(perms))
                log_activity(db, c, me, "role", role["id"], role["name"], "permissions",
                             f"Cambió los permisos del rol «{role['name']}»"
                             + (f": +{len(added)}" if added else "") + (f" −{len(removed)}" if removed else ""),
                             {"added": added, "removed": removed})
            if "onboarding" in data:
                items = [clean_text(i, "paso", 200) for i in data["onboarding"] if str(i).strip()][:30]
                c.execute("INSERT INTO onboarding_templates(role_id, items) VALUES (?, ?) ON CONFLICT(role_id) "
                          "DO UPDATE SET items=excluded.items", (role["id"], json.dumps(items, ensure_ascii=False)))
            permissions_changed(c)
        return {"ok": True}

    @router.route("DELETE", "/api/admin/roles/{rid}", perm="sistema.usuarios", idempotent=True)
    def delete_role(req, rid):
        with db.tx() as c:
            role = c.execute("SELECT * FROM roles WHERE id=?", (rid,)).fetchone()
            if not role:
                raise not_found("El rol no existe.")
            if role["locked"]:
                raise forbidden("El rol de administrador no se puede eliminar.")
            if c.execute("SELECT COUNT(*) FROM users WHERE role_id=?", (role["id"],)).fetchone()[0]:
                raise bad_request("No se puede eliminar un rol que tienen asignado usuarios. Cámbiales el rol primero.")
            c.execute("DELETE FROM roles WHERE id=?", (role["id"],))
            log_activity(db, c, req.user, "role", role["id"], role["name"], "deleted", f"Eliminó el rol «{role['name']}»")
            permissions_changed(c)
        return {"ok": True}

    # -- proyectos ------------------------------------------------------------------------------
    @router.route("GET", "/api/projects")
    def projects(req):
        ids = visible_project_ids(db, req.user)
        rows = db.query("SELECT * FROM projects ORDER BY archived, name")
        out = []
        for r in rows:
            if ids is not None and r["id"] not in ids:
                continue
            r["member_ids"] = [x["user_id"] for x in db.query("SELECT user_id FROM project_members WHERE project_id=?",
                                                              (r["id"],))]
            r["open_incidents"] = db.scalar("SELECT COUNT(*) FROM incidents WHERE project_id=? AND status IN "
                                            "('pendiente','en_progreso','bloqueada','en_verificacion')", (r["id"],))
            r["open_tasks"] = db.scalar("SELECT COUNT(*) FROM tasks WHERE project_id=? AND status<>'resuelto'",
                                        (r["id"],))
            r["version"] = r["row_version"]
            out.append(r)
        return {"items": out, "default_project": int(db.meta("default_project") or 0) or None}

    @router.route("POST", "/api/projects", perm="proyectos.gestionar", idempotent=True)
    def new_project(req):
        data = req.json
        key = clean_text(data.get("key"), "prefijo", 8, required=True).upper()
        if not KEY_RE.match(key):
            raise bad_request("El prefijo debe tener de 2 a 8 letras o números y empezar por una letra (ej. APP, WEB).")
        name = clean_text(data.get("name"), "nombre", 80, required=True)
        color = data.get("color") or "#5b8def"
        if not COLOR_RE.match(color):
            raise bad_request("Color no válido.")
        with db.tx() as c:
            uid = clean_uid(data.get("uid"))
            dup = c.execute("SELECT id FROM projects WHERE uid=?", (uid,)).fetchone()
            if dup:
                return {"id": dup[0], "duplicate": True}
            if c.execute("SELECT 1 FROM projects WHERE key=?", (key,)).fetchone():
                raise bad_request(f"Ya existe un proyecto con el prefijo {key}.")
            ts = now_iso()
            cur = c.execute("INSERT INTO projects(uid, key, name, description, color, created_by, created_at, updated_at) "
                            "VALUES (?,?,?,?,?,?,?,?)", (uid, key, name, clean_text(data.get("description"), "descripción",
                                                                                   2000), color, req.user["id"], ts, ts))
            pid = cur.lastrowid
            members = set(clean_int(m, "miembro") for m in data.get("member_ids") or []) | {req.user["id"]}
            c.executemany("INSERT OR IGNORE INTO project_members(project_id, user_id) VALUES (?,?)",
                          [(pid, m) for m in members if c.execute("SELECT 1 FROM users WHERE id=?", (m,)).fetchone()])
            c.execute("INSERT INTO channels(uid, name, kind, project_id, created_by, created_at) VALUES (?,?,?,?,?,?)",
                      (app.new_uid(), name, "proyecto", pid, req.user["id"], ts))
            if not db.meta("default_project"):
                db.set_meta(c, "default_project", str(pid))
            log_activity(db, c, req.user, "project", pid, key, "created", f"Creó el proyecto {name} ({key})",
                         project_id=pid)
            db.emit(c, "project", "project", pid, actor_id=req.user["id"])
            db.emit(c, "permissions")
        return Response.json({"id": pid}, 201)

    @router.route("PATCH", "/api/projects/{pid}", perm="proyectos.gestionar", idempotent=True)
    def edit_project(req, pid):
        data = req.json
        with db.tx() as c:
            row = c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
            if not row:
                raise not_found("El proyecto no existe.")
            sets, vals = [], []
            if "name" in data:
                sets.append("name=?")
                vals.append(clean_text(data["name"], "nombre", 80, required=True))
            if "description" in data:
                sets.append("description=?")
                vals.append(clean_text(data["description"], "descripción", 2000))
            if "color" in data:
                if not COLOR_RE.match(data["color"] or ""):
                    raise bad_request("Color no válido.")
                sets.append("color=?")
                vals.append(data["color"])
            if "key" in data:
                key = clean_text(data["key"], "prefijo", 8, required=True).upper()
                if key != row["key"]:
                    if not KEY_RE.match(key):
                        raise bad_request("Prefijo no válido.")
                    if c.execute("SELECT 1 FROM incidents WHERE project_id=? LIMIT 1", (row["id"],)).fetchone():
                        raise bad_request("No se puede cambiar el prefijo de un proyecto que ya tiene incidencias: sus "
                                          "identificadores son permanentes.")
                    if c.execute("SELECT 1 FROM projects WHERE key=? AND id<>?", (key, row["id"])).fetchone():
                        raise bad_request(f"Ya existe un proyecto con el prefijo {key}.")
                    sets.append("key=?")
                    vals.append(key)
            if "archived" in data:
                sets.append("archived=?")
                vals.append(1 if data["archived"] else 0)
            if sets:
                c.execute(f"UPDATE projects SET {', '.join(sets)}, updated_at=?, row_version=row_version+1 WHERE id=?",
                          vals + [now_iso(), row["id"]])
            if "member_ids" in data:
                members = {clean_int(m, "miembro") for m in data["member_ids"] or []}
                before = {r[0] for r in c.execute("SELECT user_id FROM project_members WHERE project_id=?",
                                                  (row["id"],))}
                c.execute("DELETE FROM project_members WHERE project_id=?", (row["id"],))
                c.executemany("INSERT OR IGNORE INTO project_members(project_id, user_id) VALUES (?,?)",
                              [(row["id"], m) for m in members if c.execute("SELECT 1 FROM users WHERE id=?",
                                                                            (m,)).fetchone()])
                added = {r[0] for r in c.execute("SELECT user_id FROM project_members WHERE project_id=?",
                                                 (row["id"],))} - before
                announce_projects(c, req.user, sorted(added), [row["id"]])
                db.emit(c, "permissions")
            if data.get("make_default"):
                db.set_meta(c, "default_project", str(row["id"]))
            log_activity(db, c, req.user, "project", row["id"], row["key"], "edited",
                         f"Actualizó el proyecto {row['name']}", project_id=row["id"])
            db.emit(c, "project", "project", row["id"], actor_id=req.user["id"])
        return {"ok": True}

    # -- tiempo -------------------------------------------------------------------------------------
    def running_entry(user_id):
        return db.query_one("SELECT * FROM time_entries WHERE user_id=? AND ended_at IS NULL", (user_id,))

    def stop_entry(c, entry, ts=None):
        ts = ts or now_iso()
        secs = seconds_between(entry["started_at"], ts)
        c.execute("UPDATE time_entries SET ended_at=?, seconds=? WHERE id=?", (ts, secs, entry["id"]))
        return secs

    def describe(entry) -> dict:
        from .relations import summary
        s = summary(db, entry["entity_type"], entry["entity_id"]) or {}
        return {**entry, "key": s.get("key"), "title": s.get("title")}

    @router.route("GET", "/api/time/current")
    def time_current(req):
        e = running_entry(req.user["id"])
        return {"entry": describe(e) if e else None}

    @router.route("POST", "/api/time/start", idempotent=True)
    def time_start(req):
        data = req.json
        user = req.user
        etype = data.get("entity_type")
        if etype not in ("incident", "task"):
            raise bad_request("Solo se puede registrar tiempo en incidencias y tareas.")
        auth.require_any(user, "tareas.trabajar", "incidencias.trabajar")
        target = find_entity(db, user, etype, data.get("entity") or "")
        with db.tx() as c:
            cur = c.execute("SELECT * FROM time_entries WHERE user_id=? AND ended_at IS NULL", (user["id"],)).fetchone()
            if cur:
                cur = dict(cur)
                if cur["entity_type"] == etype and cur["entity_id"] == target["id"]:
                    return {"entry": describe(cur), "already_running": True}
                if not data.get("stop_running"):
                    raise HttpError(409, "cronometro_activo", "Ya tienes un cronómetro en marcha. ¿Quieres pararlo y "
                                                              "empezar este?", {"running": describe(cur)})
                stop_entry(c, cur)
            ts = now_iso()
            cid = c.execute("INSERT INTO time_entries(user_id, entity_type, entity_id, started_at, source, created_at) "
                            "VALUES (?,?,?,?, 'timer', ?)", (user["id"], etype, target["id"], ts, ts)).lastrowid
            db.emit(c, "time", etype, target["id"], actor_id=user["id"])
        return {"entry": describe(db.query_one("SELECT * FROM time_entries WHERE id=?", (cid,)))}

    @router.route("POST", "/api/time/stop", idempotent=True)
    def time_stop(req):
        with db.tx() as c:
            e = c.execute("SELECT * FROM time_entries WHERE user_id=? AND ended_at IS NULL", (req.user["id"],)).fetchone()
            if not e:
                return {"stopped": False}
            secs = stop_entry(c, dict(e), None)
            if req.json.get("note"):
                c.execute("UPDATE time_entries SET note=? WHERE id=?", (clean_text(req.json["note"], "nota", 500), e["id"]))
            db.emit(c, "time", e["entity_type"], e["entity_id"], actor_id=req.user["id"])
        return {"stopped": True, "seconds": secs}

    @router.route("POST", "/api/time", idempotent=True)
    def time_manual(req):
        data = req.json
        user = req.user
        etype = data.get("entity_type")
        if etype not in ("incident", "task"):
            raise bad_request("Solo se puede registrar tiempo en incidencias y tareas.")
        auth.require_any(user, "tareas.trabajar", "incidencias.trabajar")
        target = find_entity(db, user, etype, data.get("entity") or "")
        day = clean_date(data.get("date"), "fecha") or date.today().isoformat()
        minutes = clean_int(data.get("minutes"), "minutos", 1, 24 * 60)
        start = datetime.fromisoformat(day + "T09:00:00").replace(tzinfo=timezone.utc)
        end = start + timedelta(minutes=minutes)
        fmt = "%Y-%m-%dT%H:%M:%S.000Z"
        with db.tx() as c:
            cid = c.execute("INSERT INTO time_entries(user_id, entity_type, entity_id, started_at, ended_at, seconds, note, "
                            "source, created_at) VALUES (?,?,?,?,?,?,?, 'manual', ?)",
                            (user["id"], etype, target["id"], start.strftime(fmt), end.strftime(fmt), minutes * 60,
                             clean_text(data.get("note"), "nota", 500), now_iso())).lastrowid
            db.emit(c, "time", etype, target["id"], actor_id=user["id"])
        return Response.json({"id": cid}, 201)

    @router.route("DELETE", "/api/time/{tid}", idempotent=True)
    def time_delete(req, tid):
        with db.tx() as c:
            e = c.execute("SELECT * FROM time_entries WHERE id=?", (tid,)).fetchone()
            if not e:
                raise not_found("El registro no existe.")
            if e["user_id"] != req.user["id"]:
                raise forbidden("Solo puedes borrar tus propios registros de tiempo.")
            c.execute("DELETE FROM time_entries WHERE id=?", (e["id"],))
            db.emit(c, "time", e["entity_type"], e["entity_id"], actor_id=req.user["id"])
        return {"ok": True}

    @router.route("GET", "/api/time")
    def time_list(req):
        user_id = req.q_int("user_id") or req.user["id"]
        if user_id != req.user["id"]:
            auth.require_any(req.user, "equipo.ver", "informes.ver")
        start = clean_date(req.q("from"), "desde") or (date.today() - timedelta(days=6)).isoformat()
        end = clean_date(req.q("to"), "hasta") or date.today().isoformat()
        rows = db.query("SELECT * FROM time_entries WHERE user_id=? AND started_at >= ? AND started_at <= ? ORDER BY "
                        "started_at DESC", (user_id, start, end + "T23:59:59.999Z"))
        items = [describe(r) for r in rows]
        return {"items": items, "total_seconds": sum(r["seconds"] or 0 for r in rows)}

    # -- ausencias y carga de trabajo -------------------------------------------------------------
    @router.route("GET", "/api/absences")
    def absences(req):
        un = names(db.conn)
        rows = db.query("SELECT * FROM absences WHERE end_date >= ? ORDER BY start_date",
                        ((date.today() - timedelta(days=60)).isoformat(),))
        if not auth.has_perm(req.user, "equipo.ver"):
            rows = [r for r in rows if r["user_id"] == req.user["id"]]
        for r in rows:
            r["user_name"] = un.get(r["user_id"])
        return {"items": rows}

    @router.route("POST", "/api/absences", idempotent=True)
    def new_absence(req):
        data = req.json
        uid = clean_int(data.get("user_id") or req.user["id"], "persona")
        if uid != req.user["id"]:
            auth.require(req.user, "sistema.usuarios")
        start = clean_date(data.get("start_date"), "inicio")
        end = clean_date(data.get("end_date"), "fin") or start
        if not start or end < start:
            raise bad_request("Fechas de ausencia no válidas.")
        with db.tx() as c:
            cid = c.execute("INSERT INTO absences(user_id, start_date, end_date, note, created_by, created_at) "
                            "VALUES (?,?,?,?,?,?)", (uid, start, end, clean_text(data.get("note"), "nota", 200),
                                                     req.user["id"], now_iso())).lastrowid
            log_activity(db, c, req.user, "absence", cid, None, "created", f"Registró una ausencia del {start} al {end}")
            db.emit(c, "absence", "absence", cid, actor_id=req.user["id"])
        return Response.json({"id": cid}, 201)

    @router.route("DELETE", "/api/absences/{aid}", idempotent=True)
    def delete_absence(req, aid):
        with db.tx() as c:
            a = c.execute("SELECT * FROM absences WHERE id=?", (aid,)).fetchone()
            if not a:
                raise not_found("La ausencia no existe.")
            if a["user_id"] != req.user["id"]:
                auth.require(req.user, "sistema.usuarios")
            c.execute("DELETE FROM absences WHERE id=?", (a["id"],))
            db.emit(c, "absence", "absence", a["id"], actor_id=req.user["id"])
        return {"ok": True}

    def workload_for(week: str | None) -> dict:
        ws, we = week_bounds(week)
        un_rows = db.query("SELECT id, display_name, capacity_hours FROM users WHERE active=1 ORDER BY display_name")
        out = []
        for u in un_rows:
            absent_days = 0
            for a in db.query("SELECT start_date, end_date FROM absences WHERE user_id=? AND end_date >= ? AND "
                              "start_date <= ?", (u["id"], ws.isoformat(), we.isoformat())):
                d = max(date.fromisoformat(a["start_date"]), ws)
                last = min(date.fromisoformat(a["end_date"]), we)
                while d <= last:
                    if d.weekday() < 5:
                        absent_days += 1
                    d += timedelta(days=1)
            capacity = max(0.0, (u["capacity_hours"] or 0) * (5 - min(5, absent_days)) / 5)
            items = []
            for t in db.query("SELECT key, title, estimate_hours, status FROM tasks WHERE assignee_id=? AND "
                              "status IN ('pendiente','en_progreso','bloqueada') AND (planned_week=? OR target_date "
                              "BETWEEN ? AND ? OR status='en_progreso')", (u["id"], ws.isoformat(), ws.isoformat(),
                                                                         we.isoformat())):
                items.append({"type": "task", **t})
            for i in db.query("SELECT key, title, estimate_hours, status FROM incidents WHERE assignee_id=? AND status IN "
                              "('pendiente','en_progreso','bloqueada')", (u["id"],)):
                items.append({"type": "incident", **i})
            assigned = round(sum((x["estimate_hours"] or 0) for x in items), 1)
            logged = db.scalar("SELECT COALESCE(SUM(seconds), 0) FROM time_entries WHERE user_id=? AND started_at >= ? "
                               "AND started_at <= ?", (u["id"], ws.isoformat(), we.isoformat() + "T23:59:59.999Z")) or 0
            out.append({"user_id": u["id"], "user_name": u["display_name"], "capacity_hours": round(capacity, 1),
                        "absent_days": absent_days, "assigned_hours": assigned, "free_hours": round(capacity - assigned, 1),
                        "logged_hours": round(logged / 3600, 1), "over": assigned > capacity,
                        "unestimated": sum(1 for x in items if not x["estimate_hours"]), "items": items})
        return {"week_start": ws.isoformat(), "week_end": we.isoformat(), "people": out}

    @router.route("GET", "/api/workload", perm="equipo.ver")
    def workload(req):
        return workload_for(req.q("week"))

    @router.route("GET", "/api/workload/check")
    def workload_check(req):
        """Aviso antes de asignar trabajo por encima de la capacidad de alguien."""
        uid = req.q_int("user_id")
        hours = float(req.q("hours") or 0)
        data = workload_for(req.q("week"))
        person = next((p for p in data["people"] if p["user_id"] == uid), None)
        if not person:
            raise not_found("La persona no existe.")
        return {"free_hours": person["free_hours"], "over": hours > person["free_hours"], "user_name": person["user_name"]}

    # -- daily asíncrono ----------------------------------------------------------------------------
    @router.route("GET", "/api/standups")
    def standups(req):
        day = clean_date(req.q("date"), "fecha") or date.today().isoformat()
        rows = db.query("SELECT s.*, u.display_name FROM standups s JOIN users u ON u.id=s.user_id WHERE s.date=?", (day,))
        if not auth.has_perm(req.user, "equipo.ver"):
            rows = [r for r in rows if r["user_id"] == req.user["id"]]
            missing = []
        else:
            filled = {r["user_id"] for r in rows}
            missing = [{"user_id": u["id"], "user_name": u["display_name"]} for u in
                       db.query("SELECT id, display_name FROM users WHERE active=1") if u["id"] not in filled]
        return {"date": day, "items": rows, "missing": missing}

    @router.route("GET", "/api/standups/suggest")
    def standup_suggest(req):
        """Sugerencias a partir de la actividad real de ayer y de lo que hay en curso."""
        uid = req.user["id"]
        last = db.query_one("SELECT MAX(date) d FROM standups WHERE user_id=? AND date < ?", (uid, date.today().isoformat()))
        since = (last["d"] if last and last["d"] else (date.today() - timedelta(days=1)).isoformat())
        acts = db.query("SELECT summary FROM activity WHERE user_id=? AND created_at >= ? AND action IN ('created','resolved',"
                        "'verified','completed','verification_requested','status','comment','started') ORDER BY id",
                        (uid, since))
        doing = db.query("SELECT key, title FROM tasks WHERE assignee_id=? AND status='en_progreso' UNION ALL SELECT key, "
                         "title FROM incidents WHERE assignee_id=? AND status='en_progreso'", (uid, uid))
        blocked = db.query("SELECT key, title FROM tasks WHERE assignee_id=? AND status='bloqueada' UNION ALL SELECT key, "
                           "title FROM incidents WHERE assignee_id=? AND status='bloqueada'", (uid, uid))
        seen = []
        for a in acts:
            if a["summary"] not in seen:
                seen.append(a["summary"])
        return {"yesterday": "\n".join(f"- {s}" for s in seen[:12]),
                "today": "\n".join(f"- {d['key']}: {d['title']}" for d in doing),
                "blockers": "\n".join(f"- {b['key']}: {b['title']}" for b in blocked)}

    @router.route("POST", "/api/standups", idempotent=True)
    def save_standup(req):
        data = req.json
        user = req.user
        day = clean_date(data.get("date"), "fecha") or date.today().isoformat()
        fields = {k: clean_text(data.get(k), k, 5000) for k in ("yesterday", "today", "blockers")}
        with db.tx() as c:
            ts = now_iso()
            c.execute("INSERT INTO standups(user_id, date, yesterday, today, blockers, created_at, updated_at) "
                      "VALUES (?,?,?,?,?,?,?) ON CONFLICT(user_id, date) DO UPDATE SET yesterday=excluded.yesterday, "
                      "today=excluded.today, blockers=excluded.blockers, updated_at=excluded.updated_at",
                      (user["id"], day, fields["yesterday"], fields["today"], fields["blockers"], ts, ts))
            log_activity(db, c, user, "standup", None, day, "saved", f"Rellenó su daily del {day}")
            if fields["blockers"].strip():
                notify(db, c, users_with_perm(c, "equipo.ver"), "standup",
                       f"{user['display_name']} declara un bloqueo en su daily", fields["blockers"][:300],
                       f"standup-block-{user['id']}-{day}", urgent=True, exclude=user["id"])
            db.emit(c, "standup", "standup", None, actor_id=user["id"])
        return {"ok": True}

    # -- en qué trabaja cada uno ---------------------------------------------------------------------
    @router.route("GET", "/api/team/now")
    def team_now(req):
        auth.require_any(req.user, "actividad.ver_todos", "actividad.ver_propia")
        all_users = auth.has_perm(req.user, "actividad.ver_todos")
        users = db.query("SELECT * FROM users WHERE active=1 ORDER BY display_name")
        if not all_users:
            users = [u for u in users if u["id"] == req.user["id"]]
        today = date.today().isoformat()
        out = []
        for u in users:
            uid = u["id"]
            doing = db.query("SELECT 'task' AS type, id, key, title, status FROM tasks WHERE assignee_id=? AND status IN "
                             "('en_progreso','bloqueada','en_verificacion') UNION ALL SELECT 'incident', id, key, title, "
                             "status FROM incidents WHERE assignee_id=? AND status IN ('en_progreso','bloqueada',"
                             "'en_verificacion')", (uid, uid))
            timer = running_entry(uid)
            out.append({
                "user_id": uid, "user_name": u["display_name"], "online": app.presence.is_online(uid),
                "last_seen_at": app.presence.last_seen_iso(uid) or u["last_seen_at"], "doing": doing,
                "timer": describe(timer) if timer else None,
                "today_seconds": db.scalar("SELECT COALESCE(SUM(COALESCE(seconds, 0)), 0) FROM time_entries WHERE "
                                           "user_id=? AND started_at >= ?", (uid, today)),
                "recent": db.query("SELECT summary, created_at, entity_type, entity_key, entity_id FROM activity "
                                   "WHERE user_id=? ORDER BY id DESC LIMIT 6", (uid,)),
                "standup": db.query_one("SELECT today, blockers FROM standups WHERE user_id=? AND date=?", (uid, today)),
            })
        return {"items": out, "all": all_users}

    # -- incorporación ----------------------------------------------------------------------------------
    def onboarding_for(user_id: int, role_id: int) -> list[dict]:
        tpl = db.query_one("SELECT items FROM onboarding_templates WHERE role_id=?", (role_id,))
        items = json.loads(tpl["items"]) if tpl else []
        done = {r["item"]: r["done_at"] for r in db.query("SELECT item, done_at FROM onboarding_progress WHERE user_id=?",
                                                          (user_id,))}
        return [{"item": it, "done_at": done.get(it)} for it in items]

    @router.route("GET", "/api/onboarding")
    def my_onboarding(req):
        return {"items": onboarding_for(req.user["id"], req.user["role_id"])}

    @router.route("POST", "/api/onboarding")
    def toggle_onboarding(req):
        item = clean_text(req.json.get("item"), "paso", 200, required=True)
        with db.tx() as c:
            if req.json.get("done", True):
                c.execute("INSERT INTO onboarding_progress(user_id, item, done_at) VALUES (?,?,?) ON CONFLICT(user_id, item) "
                          "DO UPDATE SET done_at=excluded.done_at", (req.user["id"], item, now_iso()))
            else:
                c.execute("DELETE FROM onboarding_progress WHERE user_id=? AND item=?", (req.user["id"], item))
        return {"items": onboarding_for(req.user["id"], req.user["role_id"])}

    @router.route("GET", "/api/admin/onboarding", perm="sistema.usuarios")
    def onboarding_overview(req):
        out = []
        for u in db.query("SELECT id, display_name, role_id FROM users WHERE active=1"):
            items = onboarding_for(u["id"], u["role_id"])
            done = sum(1 for i in items if i["done_at"])
            out.append({"user_id": u["id"], "user_name": u["display_name"], "done": done, "total": len(items),
                        "percent": round(100 * done / len(items)) if items else 100, "items": items})
        return {"items": out}

    _ = parse_iso
