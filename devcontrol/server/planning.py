"""Tareas, ideas, versiones (notas de versión), roadmap con previsión, plan semanal,
fechas importantes, renovaciones y calendario."""
from __future__ import annotations

import calendar as _cal
import json
from datetime import date, timedelta

from . import auth
from .common import (
    IDEA_STATUSES,
    LIST_PREVIEW_CHARS,
    LONG_TEXT_MAX,
    PRIORITIES,
    TASK_STATUSES,
    VERSION_STATUSES,
    check_version,
    clean_choice,
    clean_date,
    clean_float,
    clean_int,
    clean_labels,
    clean_text,
    clean_uid,
    default_project_id,
    log_activity,
    mentioned_users,
    names,
    notify,
    project_clause,
    require_project,
    user_exists,
    users_with_perm,
    visible_project_ids,
)
from .db import now_iso, parse_iso, seconds_between
from .entities import find_entity
from .web import Response, bad_request, forbidden, not_found

RECURRENCE_FREQS = {"daily": "Cada día", "weekly": "Cada semana", "monthly": "Cada mes", "yearly": "Cada año",
                    "after_days": "N días después de completarla"}


def monday_of(value: str | None) -> str | None:
    if not value:
        return None
    d = date.fromisoformat(value)
    return (d - timedelta(days=d.weekday())).isoformat()


def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, _cal.monthrange(y, m)[1]))


def next_occurrence(rule: dict, base: date, completed: date) -> date:
    n = max(1, int(rule.get("interval") or 1))
    freq = rule.get("freq")
    if freq == "daily":
        return base + timedelta(days=n)
    if freq == "weekly":
        return base + timedelta(weeks=n)
    if freq == "monthly":
        return add_months(base, n)
    if freq == "yearly":
        return add_months(base, 12 * n)
    return completed + timedelta(days=n)


def clean_recurrence(value) -> str | None:
    if value in (None, "", {}):
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise bad_request("Repetición no válida.") from None
    if not isinstance(value, dict) or value.get("freq") not in RECURRENCE_FREQS:
        raise bad_request("Repetición no válida.")
    return json.dumps({"freq": value["freq"], "interval": clean_int(value.get("interval") or 1, "intervalo", 1, 365)})


def clean_checklist(value) -> str:
    if value in (None, ""):
        return "[]"
    if not isinstance(value, list):
        raise bad_request("La lista de comprobación no es válida.")
    items = []
    for it in value[:50]:
        if isinstance(it, dict) and str(it.get("text", "")).strip():
            items.append({"text": clean_text(it["text"], "elemento de la lista", 300), "done": bool(it.get("done"))})
    return json.dumps(items, ensure_ascii=False)


def register(router, app):
    db = app.db

    def vmap():
        return {r["id"]: r["name"] for r in db.query("SELECT id, name FROM versions")}

    def pmap():
        return {r["id"]: r for r in db.query("SELECT id, key, name, color FROM projects")}

    def ser_task(r: dict, un: dict, vn: dict, pm: dict, extra: dict | None = None) -> dict:
        r = dict(r)
        r["priority_label"] = PRIORITIES.get(r["priority"])
        r["status_label"] = TASK_STATUSES.get(r["status"])
        r["version_name"] = vn.get(r["version_id"])
        p = pm.get(r["project_id"]) or {}
        r["project_key"], r["project_name"], r["project_color"] = p.get("key"), p.get("name"), p.get("color")
        r["created_by_name"] = un.get(r["created_by"])
        r["updated_by_name"] = un.get(r["updated_by"])
        r["assignee_name"] = un.get(r["assignee_id"]) if r["assignee_id"] else None
        r["checklist"] = json.loads(r.get("checklist") or "[]")
        r["recurrence"] = json.loads(r["recurrence"]) if r.get("recurrence") else None
        r["labels_list"] = [x for x in (r.get("labels") or "").split(",") if x]
        r["version"] = r["row_version"]
        r["overdue"] = bool(r["target_date"] and r["status"] != "resuelto" and r["target_date"] < date.today().isoformat())
        if extra:
            r.update(extra)
        return r

    app.ser_task = lambda r: ser_task(r, names(db.conn), vmap(), pmap())

    def version_ok(c, vid, project_id):
        if vid in (None, "", 0):
            return None
        vid = clean_int(vid, "versión")
        if not c.execute("SELECT 1 FROM versions WHERE id=? AND project_id=?", (vid, project_id)).fetchone():
            raise bad_request("La versión indicada no pertenece al proyecto.")
        return vid

    # -- tareas ------------------------------------------------------------------------
    def list_tasks(req, user) -> list[dict]:
        pclause, params = project_clause(db, user, "project_id", req)
        where = [pclause]
        for name, choices in (("status", TASK_STATUSES), ("priority", PRIORITIES)):
            if req.q(name):
                vals = req.q(name).split(",")
                for v in vals:
                    if v not in choices:
                        raise bad_request(f"Filtro no válido: {name}={v}")
                where.append(f"{name} IN ({','.join('?' * len(vals))})")
                params += vals
        if req.q("version_id"):
            if req.q("version_id") == "none":
                where.append("version_id IS NULL")
            else:
                where.append("version_id = ?")
                params.append(req.q_int("version_id"))
        assignee = req.q("assignee")
        if assignee == "me":
            where.append("assignee_id = ?")
            params.append(user["id"])
        elif assignee == "none":
            where.append("assignee_id IS NULL")
        elif assignee:
            where.append("assignee_id = ?")
            params.append(clean_int(assignee, "responsable"))
        if req.q("to_verify") == "1":
            where.append("status = 'en_verificacion'")
            if "tareas.verificar_todas" not in user["permissions"]:
                where.append("created_by = ?")
                params.append(user["id"])
        if req.q("week"):
            where.append("planned_week = ?")
            params.append(monday_of(req.q("week")))
        if req.q("label"):
            where.append("(',' || labels || ',') LIKE ?")
            params.append(f"%,{req.q('label').lower()},%")
        if req.q("q"):
            like = f"%{req.q('q')}%"
            where.append("(title LIKE ? OR description LIKE ? OR key LIKE ?)")
            params += [like, like, like]
        sql = "SELECT * FROM tasks WHERE " + " AND ".join(where) + \
            " ORDER BY CASE priority WHEN 'critica' THEN 0 WHEN 'alta' THEN 1 WHEN 'media' THEN 2 ELSE 3 END, id DESC"
        rows = db.query(sql, params)
        un, vn, pm = names(db.conn), vmap(), pmap()
        blocked = {}
        ids = [r["id"] for r in rows]
        if ids:
            for d in db.query(f"SELECT blocked_id, blocker_type, blocker_id FROM dependencies WHERE blocked_type='task' "
                              f"AND blocked_id IN ({','.join('?' * len(ids))})", ids):
                blocked.setdefault(d["blocked_id"], []).append(d)
        out = []
        for r in rows:
            open_deps = [d for d in blocked.get(r["id"], []) if not is_closed(d["blocker_type"], d["blocker_id"])]
            out.append(ser_task(r, un, vn, pm, {"open_blockers": len(open_deps)}))
        return out

    def is_closed(etype, eid) -> bool:
        table = "incidents" if etype == "incident" else "tasks"
        st = db.scalar(f"SELECT status FROM {table} WHERE id=?", (eid,))
        return st in ("resuelto", "resuelta", "duplicada")

    app.list_tasks = list_tasks

    @router.route("GET", "/api/tasks", perm="tareas.ver")
    def tasks(req):
        items = list_tasks(req, req.user)
        for it in items:   # descripciones muy largas: en el listado solo el principio
            if len(it.get("description") or "") > LIST_PREVIEW_CHARS:
                it["description"] = it["description"][:LIST_PREVIEW_CHARS] + "…"
                it["description_truncated"] = True
        return {"items": items}

    @router.route("GET", "/api/tasks/{tid}", perm="tareas.ver")
    def task_detail(req, tid):
        row = find_entity(db, req.user, "task", tid)
        extras = app.entity_extras("task", row["id"], req.user)
        resolutions = db.query("SELECT * FROM resolutions WHERE entity_type='task' AND entity_id=? ORDER BY id",
                               (row["id"],))
        un = names(db.conn)
        for r in resolutions:
            r["resolved_by_name"] = un.get(r["resolved_by"])
            r["verified_by_name"] = un.get(r["verified_by"]) if r["verified_by"] else None
        can_verify = row["status"] == "en_verificacion" and (
            row["created_by"] == req.user["id"] or "tareas.verificar_todas" in req.user["permissions"])
        src = None
        if row["source_message_id"]:
            from .relations import summary
            src = summary(db, "message", row["source_message_id"])
        return {"item": ser_task(row, un, vmap(), pmap(), {**extras, "resolutions": resolutions,
                                                            "can_verify": can_verify, "source_message": src})}

    def task_fields(c, data: dict, partial: bool, project_id: int) -> dict:
        spec = {
            "title": lambda v: clean_text(v, "título", 200, required=True),
            "description": lambda v: clean_text(v, "descripción", LONG_TEXT_MAX),
            "priority": lambda v: clean_choice(v, "prioridad", PRIORITIES, "media"),
            "target_date": lambda v: clean_date(v, "fecha objetivo"),
            "planned_week": lambda v: monday_of(clean_date(v, "semana prevista")),
            "version_id": lambda v: version_ok(c, v, project_id),
            "progress": lambda v: clean_int(v if v not in (None, "") else 0, "progreso", 0, 100),
            "labels": clean_labels,
            "checklist": clean_checklist,
            "estimate_hours": lambda v: clean_float(v, "estimación (horas)"),
            "recurrence": clean_recurrence,
        }
        return {k: fn(data.get(k)) for k, fn in spec.items() if k in data or not partial}

    def create_task(c, user, data: dict, idea_id=None, project_id=None) -> int:
        uid = clean_uid(data.get("uid"))
        dup = c.execute("SELECT id FROM tasks WHERE uid=?", (uid,)).fetchone()
        if dup:
            return dup[0]
        project = require_project(db, user, project_id or data.get("project_id") or default_project_id(db, user))
        f = task_fields(c, data, False, project["id"])
        assignee = user_exists(c, data.get("assignee_id"))
        if assignee and assignee != user["id"]:
            auth.require(user, "tareas.asignar")
        number = db.next_counter(c, "task")
        key = f"T-{number:03d}"
        ts = now_iso()
        src = clean_int(data.get("source_message_id"), "mensaje", optional=True)
        cur = c.execute(
            "INSERT INTO tasks(uid, project_id, number, key, title, description, priority, status, progress, labels, "
            "checklist, estimate_hours, target_date, planned_week, version_id, idea_id, assignee_id, source_message_id, "
            "recurrence, recurrence_parent_id, created_by, created_at, updated_at, updated_by) "
            "VALUES (?,?,?,?,?,?,?,'pendiente',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (uid, project["id"], number, key, f["title"], f["description"], f["priority"], f["progress"], f["labels"],
             f["checklist"], f["estimate_hours"], f["target_date"], f["planned_week"], f["version_id"], idea_id,
             assignee, src, f["recurrence"], data.get("_recurrence_parent"), user["id"], ts, ts, user["id"]))
        tid = cur.lastrowid
        log_activity(db, c, user, "task", tid, key, "created", f"Creó la tarea {key}: {f['title']}",
                     project_id=project["id"])
        if src and c.execute("SELECT 1 FROM messages WHERE id=?", (src,)).fetchone():
            c.execute("INSERT OR IGNORE INTO links(from_type, from_id, to_type, to_id, kind, created_by, created_at) "
                      "VALUES ('message', ?, 'task', ?, 'origen', ?, ?)", (src, tid, user["id"], ts))
            db.emit(c, "message", "message", src, actor_id=user["id"])
        if assignee and assignee != user["id"]:
            notify(db, c, [assignee], "assigned", f"Te han asignado {key}", f["title"], f"task-assigned-{tid}-0",
                   "task", tid, key)
        notify(db, c, mentioned_users(c, f["description"]), "mention", f"{user['display_name']} te ha mencionado en {key}",
               f["title"], f"mention-task-{tid}", "task", tid, key, exclude=user["id"])
        db.index(c, "task", tid, project["id"], key, f["title"], f"{f['description']} {f['labels']}")
        db.emit(c, "task", "task", tid, actor_id=user["id"])
        return tid

    app.create_task = create_task

    @router.route("POST", "/api/tasks", perm="tareas.crear", idempotent=True)
    def new_task(req):
        with db.tx() as c:
            tid = create_task(c, req.user, req.json)
        return Response.json({"item": app.ser_task(db.query_one("SELECT * FROM tasks WHERE id=?", (tid,)))}, 201)

    def finalize_task(c, row, user, ts, res_id=None, resolver=None):
        c.execute("UPDATE tasks SET status='resuelto', progress=100, completed_at=?, block_reason=NULL, "
                  "verification_requested_at=NULL, verification_requested_by=NULL, last_reject_reason=NULL, "
                  "updated_at=?, updated_by=?, row_version=row_version+1 WHERE id=?",
                  (ts, ts, user["id"], row["id"]))
        if res_id:
            c.execute("UPDATE resolutions SET state='verificada', verified_by=?, verified_at=?, duration_seconds=? "
                      "WHERE id=?", (user["id"], ts, seconds_between(row["created_at"], ts), res_id))
        app.unblock_dependents(c, "task", row["id"], row["key"], user)
        if row["recurrence"]:
            rule = json.loads(row["recurrence"])
            base = date.fromisoformat(row["target_date"]) if row["target_date"] else date.today()
            nxt = next_occurrence(rule, base, date.today())
            new_uid = f"rec-{row['id']}-{nxt.isoformat()}"
            create_task(c, user, {"uid": new_uid, "title": row["title"], "description": row["description"],
                                  "priority": row["priority"], "target_date": nxt.isoformat(),
                                  "labels": row["labels"], "estimate_hours": row["estimate_hours"],
                                  "checklist": [{"text": i["text"], "done": False}
                                                for i in json.loads(row["checklist"] or "[]")],
                                  "recurrence": rule, "assignee_id": row["assignee_id"],
                                  "_recurrence_parent": row["id"]}, project_id=row["project_id"])

    def can_touch(user, row) -> bool:
        return (auth.has_perm(user, "tareas.editar") or row["created_by"] == user["id"]
                or row["assignee_id"] == user["id"])

    @router.route("PATCH", "/api/tasks/{tid}", idempotent=True)
    def edit_task(req, tid):
        data = req.json
        user = req.user
        with db.tx() as c:
            row = find_entity(db, user, "task", tid)
            check_version(row, data, f"La tarea {row['key']}")
            f = task_fields(c, data, True, row["project_id"])
            if any(k in f for k in ("title", "description", "priority", "version_id", "estimate_hours", "recurrence",
                                    "labels")) and not can_touch(user, row):
                raise forbidden("Solo el creador, el responsable o quien tiene permiso de edición puede cambiar la tarea.")
            if f and not (can_touch(user, row) or auth.has_perm(user, "tareas.trabajar")):
                raise forbidden()
            ts = now_iso()
            key = row["key"]
            if "assignee_id" in data:
                new_assignee = user_exists(c, data["assignee_id"])
                if new_assignee != row["assignee_id"]:
                    if not (auth.has_perm(user, "tareas.asignar") or
                            (new_assignee == user["id"] and auth.has_perm(user, "tareas.trabajar"))):
                        raise forbidden("No tienes permiso para asignar tareas a otras personas.")
                    f["assignee_id"] = new_assignee
                    un = names(c)
                    log_activity(db, c, user, "task", row["id"], key, "assigned",
                                 f"Asignó {key} a {un.get(new_assignee, 'nadie')}" if new_assignee
                                 else f"Quitó el responsable de {key}", project_id=row["project_id"])
                    if new_assignee and new_assignee != user["id"]:
                        notify(db, c, [new_assignee], "assigned", f"Te han asignado {key}", row["title"],
                               f"task-assigned-{row['id']}-{row['row_version']}", "task", row["id"], key)
            if "status" in data:
                st = clean_choice(data["status"], "estado", TASK_STATUSES)
                if st != row["status"]:
                    auth.require(user, "tareas.trabajar")
                    if row["status"] == "en_verificacion":
                        raise bad_request(f"{key} está pendiente de verificación: verifícala o recházala.")
                    if st == "en_verificacion":
                        raise bad_request("Marca la tarea como resuelta: pasará a verificación si hace falta.")
                    f["status"] = st
                    if st == "bloqueada":
                        f["block_reason"] = clean_text(data.get("block_reason"), "motivo del bloqueo", 2000,
                                                       required=True)
                        log_activity(db, c, user, "task", row["id"], key, "blocked",
                                     f"Bloqueó la tarea {key}: {f['block_reason']}", project_id=row["project_id"])
                        notify(db, c, [row["created_by"]], "incident_blocked", f"{key} bloqueada",
                               f["block_reason"][:200], f"task-blocked-{row['id']}-{ts}", "task", row["id"], key,
                               exclude=user["id"])
                    elif st == "resuelto":
                        open_deps = app.open_blockers("task", row["id"])
                        if open_deps and not data.get("ignore_dependencies"):
                            raise bad_request(f"{key} depende de elementos sin cerrar: "
                                              + ", ".join(d["key"] for d in open_deps), "dependencias_abiertas")
                        note = clean_text(data.get("resolution_note"), "nota de resolución", 5000)
                        needs = app.settings_cache("verification_required") and user["id"] != row["created_by"]
                        cycle = 1 + (c.execute("SELECT COUNT(*) FROM resolutions WHERE entity_type='task' AND "
                                               "entity_id=?", (row["id"],)).fetchone()[0])
                        cur = c.execute("INSERT INTO resolutions(entity_type, entity_id, cycle, started_at, resolved_at, "
                                        "resolved_by, solution, state) VALUES ('task',?,?,?,?,?,?,?)",
                                        (row["id"], cycle, row["started_at"] or row["created_at"], ts, user["id"], note,
                                         "pendiente" if needs else "verificada"))
                        f.pop("status")
                        if needs:
                            c.execute("UPDATE tasks SET status='en_verificacion', resolution_note=?, "
                                      "verification_requested_at=?, verification_requested_by=?, block_reason=NULL, "
                                      "progress=100, updated_at=?, updated_by=?, row_version=row_version+1 WHERE id=?",
                                      (note, ts, user["id"], ts, user["id"], row["id"]))
                            log_activity(db, c, user, "task", row["id"], key, "verification_requested",
                                         f"Marcó {key} como hecha: pendiente de verificación",
                                         project_id=row["project_id"])
                            verifiers = {row["created_by"]} | set(users_with_perm(c, "tareas.verificar_todas",
                                                                                  row["project_id"]))
                            notify(db, c, verifiers, "verify_request", f"{key} lista para verificar",
                                   f"{user['display_name']}: {row['title']}", f"task-verify-{row['id']}-{cycle}",
                                   "task", row["id"], key, exclude=user["id"])
                        else:
                            c.execute("UPDATE tasks SET resolution_note=? WHERE id=?", (note, row["id"]))
                            finalize_task(c, row, user, ts, cur.lastrowid)
                            log_activity(db, c, user, "task", row["id"], key, "completed", f"Completó la tarea {key}",
                                         project_id=row["project_id"])
                    else:
                        if row["status"] == "bloqueada":
                            f["block_reason"] = None
                            log_activity(db, c, user, "task", row["id"], key, "unblocked",
                                         f"Quitó el bloqueo de la tarea {key}", project_id=row["project_id"])
                        if row["status"] == "resuelto":
                            f["completed_at"] = None
                            log_activity(db, c, user, "task", row["id"], key, "reopened", f"Reabrió la tarea {key}",
                                         project_id=row["project_id"])
                        if st == "en_progreso":
                            if not row["started_at"]:
                                f["started_at"] = ts
                            if not row["assignee_id"] and "assignee_id" not in f:
                                f["assignee_id"] = user["id"]
                            log_activity(db, c, user, "task", row["id"], key, "started", f"Inició la tarea {key}",
                                         project_id=row["project_id"])
                        elif st == "pendiente":
                            log_activity(db, c, user, "task", row["id"], key, "status",
                                         f"Devolvió la tarea {key} a Pendiente", project_id=row["project_id"])
                elif st == "bloqueada" and "block_reason" in data:
                    f["block_reason"] = clean_text(data.get("block_reason"), "motivo del bloqueo", 2000, required=True)
            fresh = dict(c.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone())
            changed = {k: v for k, v in f.items() if fresh.get(k) != v}
            labels = {"title": "título", "priority": "prioridad", "target_date": "fecha objetivo", "version_id": "versión",
                      "planned_week": "semana prevista", "progress": "progreso", "estimate_hours": "estimación",
                      "labels": "etiquetas", "recurrence": "repetición", "checklist": "lista de comprobación",
                      "description": "descripción"}
            for k in labels:
                if k in changed and k in data:
                    shown = changed[k]
                    if k == "priority":
                        shown = PRIORITIES[shown]
                    elif k == "version_id":
                        shown = vmap().get(shown, "sin versión")
                    elif k == "progress":
                        shown = f"{shown}%"
                    elif k in ("checklist", "description", "recurrence"):
                        shown = None
                    log_activity(db, c, user, "task", row["id"], key, f"edit_{k}",
                                 f"Cambió {labels[k]} de {key}" + (f": {shown}" if shown not in (None, "") else ""),
                                 project_id=row["project_id"])
            if changed:
                sets = ", ".join(f"{k}=?" for k in changed)
                c.execute(f"UPDATE tasks SET {sets}, updated_at=?, updated_by=?, row_version=row_version+1 WHERE id=?",
                          list(changed.values()) + [ts, user["id"], row["id"]])
            new = dict(c.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone())
            if new["row_version"] == row["row_version"]:
                return {"item": app.ser_task(new), "unchanged": True}
            db.index(c, "task", new["id"], new["project_id"], key, new["title"], f"{new['description']} {new['labels']}")
            db.emit(c, "task", "task", row["id"], actor_id=user["id"])
            return {"item": app.ser_task(new)}

    @router.route("POST", "/api/tasks/{tid}/verify", perm="tareas.ver", idempotent=True)
    def verify_task(req, tid):
        data = req.json
        user = req.user
        with db.tx() as c:
            row = find_entity(db, user, "task", tid)
            key = row["key"]
            if row["status"] != "en_verificacion":
                raise bad_request(f"{key} no está pendiente de verificación.")
            if not (row["created_by"] == user["id"] or auth.has_perm(user, "tareas.verificar_todas")):
                raise forbidden("Solo quien creó la tarea (o quien tiene permiso de verificar) puede verificarla.")
            res = c.execute("SELECT * FROM resolutions WHERE entity_type='task' AND entity_id=? AND state='pendiente' "
                            "ORDER BY id DESC LIMIT 1", (row["id"],)).fetchone()
            ts = now_iso()
            resolver = res["resolved_by"] if res else row["verification_requested_by"]
            if data.get("accept"):
                finalize_task(c, row, user, ts, res["id"] if res else None)
                log_activity(db, c, user, "task", row["id"], key, "verified", f"Verificó y cerró la tarea {key}",
                             project_id=row["project_id"])
                notify(db, c, [resolver, row["assignee_id"]], "verified", f"{key} verificada y cerrada",
                       f"{user['display_name']} ha confirmado «{row['title']}»", f"task-verified-{row['id']}-{ts}",
                       "task", row["id"], key, exclude=user["id"])
            else:
                reason = clean_text(data.get("reason"), "motivo del rechazo", 5000, required=True)
                if res:
                    c.execute("UPDATE resolutions SET state='rechazada', verified_by=?, verified_at=?, reject_reason=? "
                              "WHERE id=?", (user["id"], ts, reason, res["id"]))
                c.execute("UPDATE tasks SET status='en_progreso', last_reject_reason=?, verification_requested_at=NULL, "
                          "verification_requested_by=NULL, updated_at=?, updated_by=?, row_version=row_version+1 "
                          "WHERE id=?", (reason, ts, user["id"], row["id"]))
                c.execute("INSERT INTO comments(uid, entity_type, entity_id, user_id, body, kind, created_at) "
                          "VALUES (?, 'task', ?, ?, ?, 'rejection', ?)", (f"reject-task-{row['id']}-{ts}", row["id"],
                                                                           user["id"], reason, ts))
                log_activity(db, c, user, "task", row["id"], key, "rejected", f"Rechazó la tarea {key}: {reason}",
                             project_id=row["project_id"])
                notify(db, c, [resolver, row["assignee_id"]], "rejected", f"{key}: rechazada en la verificación",
                       f"{user['display_name']}: {reason[:200]}", f"task-rejected-{row['id']}-{ts}", "task",
                       row["id"], key, urgent=True, exclude=user["id"])
            db.emit(c, "task", "task", row["id"], actor_id=user["id"])
            new = dict(c.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone())
            return {"item": app.ser_task(new)}

    # -- ideas (con votos) --------------------------------------------------------------
    def ser_idea(r, un, vn, votes, my_votes, pm):
        r = dict(r)
        r["priority_label"] = PRIORITIES.get(r["priority"])
        r["status_label"] = IDEA_STATUSES.get(r["status"])
        r["version_name"] = vn.get(r["version_id"])
        r["created_by_name"] = un.get(r["created_by"])
        r["project_key"] = (pm.get(r["project_id"]) or {}).get("key")
        r["version"] = r["row_version"]
        r["votes"] = votes.get(r["id"], 0)
        r["voted"] = r["id"] in my_votes
        if r["task_id"]:
            t = db.query_one("SELECT key, status FROM tasks WHERE id=?", (r["task_id"],))
            r["task_key"] = t["key"] if t else None
        return r

    @router.route("GET", "/api/ideas", perm="planificacion.ver")
    def ideas(req):
        pclause, params = project_clause(db, req.user, "project_id", req)
        where = [pclause]
        if req.q("status"):
            vals = req.q("status").split(",")
            where.append(f"status IN ({','.join('?' * len(vals))})")
            params += vals
        rows = db.query("SELECT * FROM ideas WHERE " + " AND ".join(where), params)
        votes = {r["idea_id"]: r["n"] for r in db.query("SELECT idea_id, COUNT(*) n FROM idea_votes GROUP BY idea_id")}
        mine = {r["idea_id"] for r in db.query("SELECT idea_id FROM idea_votes WHERE user_id=?", (req.user["id"],))}
        un, vn, pm = names(db.conn), vmap(), pmap()
        items = [ser_idea(r, un, vn, votes, mine, pm) for r in rows]
        sort = req.q("sort") or "votes"
        prio = {"critica": 0, "alta": 1, "media": 2, "baja": 3}
        items.sort(key=lambda i: (i["status"] == "descartada",
                                  -i["votes"] if sort == "votes" else prio.get(i["priority"], 9), -i["id"]))
        return {"items": items}

    def idea_fields(c, data, partial, project_id):
        spec = {
            "title": lambda v: clean_text(v, "título", 200, required=True),
            "description": lambda v: clean_text(v, "descripción", LONG_TEXT_MAX),
            "priority": lambda v: clean_choice(v, "prioridad", PRIORITIES, "media"),
            "version_id": lambda v: version_ok(c, v, project_id),
            "status": lambda v: clean_choice(v, "estado", IDEA_STATUSES, "propuesta"),
        }
        return {k: fn(data.get(k)) for k, fn in spec.items() if k in data or not partial}

    @router.route("POST", "/api/ideas", perm="planificacion.ver", idempotent=True)
    def new_idea(req):
        data = req.json
        uid = clean_uid(data.get("uid"))
        user = req.user
        project = require_project(db, user, data.get("project_id") or default_project_id(db, user))
        with db.tx() as c:
            dup = c.execute("SELECT id FROM ideas WHERE uid=?", (uid,)).fetchone()
            if dup:
                return {"id": dup[0], "duplicate": True}
            f = idea_fields(c, data, False, project["id"])
            if not auth.has_perm(user, "planificacion.editar"):
                f["status"] = "propuesta"
            ts = now_iso()
            cur = c.execute("INSERT INTO ideas(uid, project_id, title, description, priority, version_id, status, "
                            "created_by, created_at, updated_at, updated_by) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (uid, project["id"], f["title"], f["description"], f["priority"], f["version_id"],
                             f["status"], user["id"], ts, ts, user["id"]))
            c.execute("INSERT INTO idea_votes(idea_id, user_id, created_at) VALUES (?,?,?)", (cur.lastrowid, user["id"],
                                                                                           ts))
            log_activity(db, c, user, "idea", cur.lastrowid, None, "created", f"Propuso la idea «{f['title']}»",
                         project_id=project["id"])
            db.index(c, "idea", cur.lastrowid, project["id"], "", f["title"], f["description"])
            db.emit(c, "idea", "idea", cur.lastrowid, actor_id=user["id"])
            return Response.json({"id": cur.lastrowid}, 201)

    @router.route("POST", "/api/ideas/{iid}/vote", perm="planificacion.ver", idempotent=True)
    def vote_idea(req, iid):
        user = req.user
        with db.tx() as c:
            idea = find_entity(db, user, "idea", iid)
            if req.json.get("vote", True):
                c.execute("INSERT OR IGNORE INTO idea_votes(idea_id, user_id, created_at) VALUES (?,?,?)",
                          (idea["id"], user["id"], now_iso()))
            else:
                c.execute("DELETE FROM idea_votes WHERE idea_id=? AND user_id=?", (idea["id"], user["id"]))
            db.emit(c, "idea", "idea", idea["id"], actor_id=user["id"])
        return {"votes": db.scalar("SELECT COUNT(*) FROM idea_votes WHERE idea_id=?", (idea["id"],))}

    @router.route("PATCH", "/api/ideas/{iid}", perm="planificacion.ver", idempotent=True)
    def edit_idea(req, iid):
        data = req.json
        user = req.user
        with db.tx() as c:
            row = find_entity(db, user, "idea", iid)
            if not (auth.has_perm(user, "planificacion.editar") or row["created_by"] == user["id"]):
                raise forbidden()
            check_version(row, data, "La idea")
            f = idea_fields(c, data, True, row["project_id"])
            if "status" in f and not auth.has_perm(user, "planificacion.editar"):
                raise forbidden("Solo quien gestiona la planificación puede cambiar el estado de una idea.")
            changed = {k: v for k, v in f.items() if row.get(k) != v}
            if not changed:
                return {"unchanged": True}
            sets = ", ".join(f"{k}=?" for k in changed)
            c.execute(f"UPDATE ideas SET {sets}, updated_at=?, updated_by=?, row_version=row_version+1 WHERE id=?",
                      list(changed.values()) + [now_iso(), user["id"], row["id"]])
            desc = []
            if "status" in changed:
                desc.append(f"estado: {IDEA_STATUSES[changed['status']]}")
            if "priority" in changed:
                desc.append(f"prioridad: {PRIORITIES[changed['priority']]}")
            log_activity(db, c, user, "idea", row["id"], None, "edited",
                         f"Actualizó la idea «{f.get('title', row['title'])}»" + (f" ({', '.join(desc)})" if desc else ""),
                         project_id=row["project_id"])
            new = c.execute("SELECT * FROM ideas WHERE id=?", (row["id"],)).fetchone()
            db.index(c, "idea", row["id"], row["project_id"], "", new["title"], new["description"])
            db.emit(c, "idea", "idea", row["id"], actor_id=user["id"])
            return {"ok": True}

    @router.route("POST", "/api/ideas/{iid}/to-task", perm="planificacion.editar", idempotent=True)
    def idea_to_task(req, iid):
        user = req.user
        auth.require(user, "tareas.crear")
        with db.tx() as c:
            idea = find_entity(db, user, "idea", iid)
            if idea["task_id"]:
                raise bad_request("Esta idea ya se convirtió en tarea.")
            tid = create_task(c, user, {"uid": clean_uid(req.json.get("uid")), "title": idea["title"],
                                        "description": idea["description"], "priority": idea["priority"],
                                        "version_id": idea["version_id"]}, idea_id=idea["id"],
                              project_id=idea["project_id"])
            key = c.execute("SELECT key FROM tasks WHERE id=?", (tid,)).fetchone()[0]
            c.execute("UPDATE ideas SET task_id=?, status='en_desarrollo', updated_at=?, updated_by=?, "
                      "row_version=row_version+1 WHERE id=?", (tid, now_iso(), user["id"], idea["id"]))
            log_activity(db, c, user, "idea", idea["id"], None, "to_task",
                         f"Convirtió la idea «{idea['title']}» en la tarea {key}", project_id=idea["project_id"])
            db.emit(c, "idea", "idea", idea["id"], actor_id=user["id"])
            return Response.json({"task_id": tid, "task_key": key}, 201)

    # -- versiones y notas de versión -----------------------------------------------------
    @router.route("GET", "/api/versions", perm="planificacion.ver")
    def versions(req):
        pclause, params = project_clause(db, req.user, "v.project_id", req)
        rows = db.query(f"SELECT v.*, p.key AS project_key, p.name AS project_name FROM versions v JOIN projects p "
                        f"ON p.id = v.project_id WHERE {pclause} ORDER BY CASE v.status WHEN 'publicada' THEN 1 "
                        f"ELSE 0 END, COALESCE(v.target_date, '9999'), v.id", params)
        for r in rows:
            r["version"] = r["row_version"]
            r["status_label"] = VERSION_STATUSES.get(r["status"])
            stats = db.query("SELECT status, COUNT(*) n FROM tasks WHERE version_id=? GROUP BY status", (r["id"],))
            r["task_counts"] = {s["status"]: s["n"] for s in stats}
            r["tasks_total"] = sum(r["task_counts"].values())
            r["tasks_done"] = r["task_counts"].get("resuelto", 0)
            r["ideas_count"] = db.scalar("SELECT COUNT(*) FROM ideas WHERE version_id=? AND status<>'descartada'",
                                         (r["id"],))
            r["builds"] = db.query("SELECT id, number, platform, channel, built_on FROM builds WHERE version_id=? "
                                   "ORDER BY id DESC", (r["id"],))
            r["fixed_incidents"] = db.scalar("SELECT COUNT(*) FROM incidents WHERE fixed_build_id IN (SELECT id FROM "
                                             "builds WHERE version_id=?)", (r["id"],))
        return {"items": rows}

    VERSION_SPEC = {
        "name": lambda v: clean_text(v, "nombre", 80, required=True),
        "description": lambda v: clean_text(v, "descripción", 5000),
        "start_date": lambda v: clean_date(v, "fecha de inicio"),
        "target_date": lambda v: clean_date(v, "fecha prevista"),
        "status": lambda v: clean_choice(v, "estado", VERSION_STATUSES, "planificada"),
        "release_notes": lambda v: clean_text(v, "notas de versión", 50000),
    }

    @router.route("POST", "/api/versions", perm="planificacion.editar", idempotent=True)
    def new_version(req):
        data = req.json
        uid = clean_uid(data.get("uid"))
        user = req.user
        project = require_project(db, user, data.get("project_id") or default_project_id(db, user))
        f = {k: fn(data.get(k)) for k, fn in VERSION_SPEC.items()}
        with db.tx() as c:
            dup = c.execute("SELECT id FROM versions WHERE uid=?", (uid,)).fetchone()
            if dup:
                return {"id": dup[0], "duplicate": True}
            if c.execute("SELECT 1 FROM versions WHERE project_id=? AND name=? COLLATE NOCASE",
                         (project["id"], f["name"])).fetchone():
                raise bad_request(f"Ya existe una versión «{f['name']}» en el proyecto.")
            ts = now_iso()
            cur = c.execute("INSERT INTO versions(uid, project_id, name, description, start_date, target_date, status, "
                            "release_notes, created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (uid, project["id"], f["name"], f["description"], f["start_date"], f["target_date"],
                             f["status"], f["release_notes"], user["id"], ts, ts))
            log_activity(db, c, user, "version", cur.lastrowid, f["name"], "created", f"Creó la versión {f['name']}",
                         project_id=project["id"])
            db.emit(c, "version", "version", cur.lastrowid, actor_id=user["id"])
            return Response.json({"id": cur.lastrowid}, 201)

    @router.route("PATCH", "/api/versions/{vid}", perm="planificacion.editar", idempotent=True)
    def edit_version(req, vid):
        data = req.json
        user = req.user
        with db.tx() as c:
            row = find_entity(db, user, "version", vid)
            check_version(row, data, "La versión")
            f = {k: fn(data[k]) for k, fn in VERSION_SPEC.items() if k in data}
            changed = {k: v for k, v in f.items() if row.get(k) != v}
            if not changed:
                return {"unchanged": True}
            if "name" in changed and c.execute("SELECT 1 FROM versions WHERE project_id=? AND name=? COLLATE NOCASE "
                                               "AND id<>?", (row["project_id"], changed["name"], row["id"])).fetchone():
                raise bad_request(f"Ya existe una versión «{changed['name']}» en el proyecto.")
            sets = ", ".join(f"{k}=?" for k in changed)
            c.execute(f"UPDATE versions SET {sets}, updated_at=?, row_version=row_version+1 WHERE id=?",
                      list(changed.values()) + [now_iso(), row["id"]])
            name = f.get("name", row["name"])
            extra = f" (estado: {VERSION_STATUSES[changed['status']]})" if "status" in changed else ""
            log_activity(db, c, user, "version", row["id"], name, "edited", f"Actualizó la versión {name}{extra}",
                         project_id=row["project_id"])
            db.emit(c, "version", "version", row["id"], actor_id=user["id"])
            return {"ok": True}

    @router.route("GET", "/api/versions/{vid}/release-notes", perm="planificacion.ver")
    def release_notes(req, vid):
        """Notas de versión generadas con lo resuelto y verificado en la versión."""
        v = find_entity(db, req.user, "version", vid)
        tasks = db.query("SELECT key, title FROM tasks WHERE version_id=? AND status='resuelto' ORDER BY number",
                         (v["id"],))
        incidents = db.query("SELECT key, title, category FROM incidents WHERE status='resuelta' AND fixed_build_id IN "
                             "(SELECT id FROM builds WHERE version_id=?) ORDER BY number", (v["id"],))
        ideas_done = db.query("SELECT i.title FROM ideas i JOIN tasks t ON t.id = i.task_id WHERE i.version_id=? AND "
                              "t.status='resuelto'", (v["id"],))
        lines = [f"# Versión {v['name']}", ""]
        if v["target_date"]:
            lines += [f"Fecha prevista: {v['target_date']}", ""]
        if ideas_done:
            lines += ["## Novedades"] + [f"- {i['title']}" for i in ideas_done] + [""]
        if tasks:
            lines += ["## Mejoras y trabajo realizado"] + [f"- {t['title']} ({t['key']})" for t in tasks] + [""]
        if incidents:
            lines += ["## Errores corregidos"] + [f"- {i['title']} ({i['key']})" for i in incidents] + [""]
        if len(lines) <= 3:
            lines.append("Todavía no hay trabajo verificado en esta versión.")
        return {"markdown": "\n".join(lines), "saved": v["release_notes"]}

    # -- roadmap con previsión -------------------------------------------------------------
    @router.route("GET", "/api/roadmap", perm="planificacion.ver")
    def roadmap(req):
        pclause, params = project_clause(db, req.user, "project_id", req)
        today = date.today()
        weeks = 8
        start = today - timedelta(days=7 * weeks)
        done_rows = db.query(f"SELECT completed_at FROM tasks WHERE {pclause} AND status='resuelto' AND completed_at >= ?",
                             params + [start.isoformat()])
        per_week = [0] * weeks
        for r in done_rows:
            d = parse_iso(r["completed_at"]).date()
            idx = weeks - 1 - max(0, (today - d).days) // 7  # la última semana termina hoy (incluido)
            if 0 <= idx < weeks:
                per_week[idx] += 1
        recent = per_week[-4:]
        velocity = round(sum(recent) / 4, 2)
        out = []
        for v in db.query(f"SELECT * FROM versions WHERE {pclause} AND status <> 'publicada' "
                          f"ORDER BY COALESCE(target_date, '9999')", params):
            total = db.scalar("SELECT COUNT(*) FROM tasks WHERE version_id=?", (v["id"],)) or 0
            done = db.scalar("SELECT COUNT(*) FROM tasks WHERE version_id=? AND status='resuelto'", (v["id"],)) or 0
            remaining = total - done
            series = []
            for i in range(weeks + 1):
                point = start + timedelta(days=7 * i)
                created = db.scalar("SELECT COUNT(*) FROM tasks WHERE version_id=? AND created_at <= ?",
                                    (v["id"], point.isoformat() + "T23:59:59.999Z")) or 0
                closed = db.scalar("SELECT COUNT(*) FROM tasks WHERE version_id=? AND status='resuelto' AND "
                                   "completed_at <= ?", (v["id"], point.isoformat() + "T23:59:59.999Z")) or 0
                series.append({"date": point.isoformat(), "remaining": created - closed})
            forecast = None
            margin = None
            if remaining == 0 and total:
                forecast = today.isoformat()
            elif velocity > 0:
                weeks_needed = remaining / velocity
                forecast = (today + timedelta(days=round(7 * weeks_needed))).isoformat()
                margin = (today + timedelta(days=round(7 * weeks_needed * 1.3))).isoformat()
            at_risk = bool(v["target_date"] and (forecast is None and remaining > 0 or
                                                 (forecast and forecast > v["target_date"])))
            out.append({"id": v["id"], "name": v["name"], "project_id": v["project_id"], "status": v["status"],
                        "start_date": v["start_date"], "target_date": v["target_date"], "tasks_total": total,
                        "tasks_done": done, "remaining": remaining, "forecast": forecast, "forecast_margin": margin,
                        "at_risk": at_risk, "burndown": series})
        ids = visible_project_ids(db, req.user)
        milestones = [m for m in db.query("SELECT id, title, date, project_id FROM project_dates WHERE date >= ? "
                                          "ORDER BY date LIMIT 20", (today.isoformat(),))
                      if m["project_id"] is None or ids is None or m["project_id"] in ids]
        return {"velocity": velocity, "per_week": per_week, "versions": out, "milestones": milestones}

    # -- plan semanal ---------------------------------------------------------------------
    @router.route("GET", "/api/weeks/{week}", perm="planificacion.ver")
    def week(req, week):
        ws = monday_of(clean_date(week, "semana"))
        we = (date.fromisoformat(ws) + timedelta(days=6)).isoformat()
        un, vn, pm = names(db.conn), vmap(), pmap()
        goals = db.query("SELECT * FROM goals WHERE week_start=? ORDER BY id", (ws,))
        for g in goals:
            g["done_by_name"] = un.get(g["done_by"])
            g["created_by_name"] = un.get(g["created_by"])
        pclause, params = project_clause(db, req.user, "project_id", req)
        planned = [ser_task(r, un, vn, pm) for r in db.query(f"SELECT * FROM tasks WHERE planned_week=? AND {pclause} "
                                                             f"ORDER BY id", [ws] + params)]
        due = [ser_task(r, un, vn, pm) for r in db.query(
            f"SELECT * FROM tasks WHERE target_date BETWEEN ? AND ? AND (planned_week IS NULL OR planned_week<>?) AND "
            f"{pclause} ORDER BY target_date", [ws, we, ws] + params)]
        due_incidents = db.query(f"SELECT id, key, title, priority, status, target_date FROM incidents WHERE "
                                 f"target_date BETWEEN ? AND ? AND {pclause} ORDER BY target_date", [ws, we] + params)
        goals_done = sum(1 for g in goals if g["done"])
        tasks_done = sum(1 for t in planned if t["status"] == "resuelto")
        total = len(goals) + len(planned)
        return {"week_start": ws, "week_end": we, "goals": goals, "planned_tasks": planned, "due_tasks": due,
                "due_incidents": due_incidents,
                "progress": {"goals_done": goals_done, "goals_total": len(goals), "tasks_done": tasks_done,
                             "tasks_total": len(planned),
                             "percent": round(100 * (goals_done + tasks_done) / total) if total else 0}}

    @router.route("POST", "/api/goals", perm="planificacion.editar", idempotent=True)
    def new_goal(req):
        data = req.json
        uid = clean_uid(data.get("uid"))
        title = clean_text(data.get("title"), "objetivo", 300, required=True)
        ws = monday_of(clean_date(data.get("week_start"), "semana"))
        if not ws:
            raise bad_request("Indica la semana del objetivo.")
        user = req.user
        with db.tx() as c:
            dup = c.execute("SELECT id FROM goals WHERE uid=?", (uid,)).fetchone()
            if dup:
                return {"id": dup[0], "duplicate": True}
            ts = now_iso()
            cur = c.execute("INSERT INTO goals(uid, week_start, title, created_by, created_at, updated_at) "
                            "VALUES (?,?,?,?,?,?)", (uid, ws, title, user["id"], ts, ts))
            log_activity(db, c, user, "goal", cur.lastrowid, ws, "created",
                         f"Añadió el objetivo semanal «{title}» (semana del {ws})")
            db.emit(c, "goal", "goal", cur.lastrowid, actor_id=user["id"])
            return Response.json({"id": cur.lastrowid}, 201)

    @router.route("PATCH", "/api/goals/{gid}", perm="planificacion.editar", idempotent=True)
    def edit_goal(req, gid):
        data = req.json
        user = req.user
        with db.tx() as c:
            g = c.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()
            if not g:
                raise not_found("El objetivo no existe.")
            ts = now_iso()
            if "title" in data:
                title = clean_text(data["title"], "objetivo", 300, required=True)
                if title != g["title"]:
                    c.execute("UPDATE goals SET title=?, updated_at=? WHERE id=?", (title, ts, g["id"]))
                    log_activity(db, c, user, "goal", g["id"], g["week_start"], "edited",
                                 f"Cambió el objetivo «{g['title']}» por «{title}»")
            if "done" in data:
                done = 1 if data["done"] else 0
                if done != g["done"]:
                    c.execute("UPDATE goals SET done=?, done_at=?, done_by=?, updated_at=? WHERE id=?",
                              (done, ts if done else None, user["id"] if done else None, ts, g["id"]))
                    log_activity(db, c, user, "goal", g["id"], g["week_start"], "done" if done else "undone",
                                 f"{'Cumplió' if done else 'Desmarcó'} el objetivo «{g['title']}»")
            db.emit(c, "goal", "goal", g["id"], actor_id=user["id"])
            return {"ok": True}

    @router.route("DELETE", "/api/goals/{gid}", perm="planificacion.editar", idempotent=True)
    def delete_goal(req, gid):
        user = req.user
        with db.tx() as c:
            g = c.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()
            if not g:
                raise not_found("El objetivo no existe.")
            c.execute("DELETE FROM goals WHERE id=?", (g["id"],))
            log_activity(db, c, user, "goal", g["id"], g["week_start"], "deleted",
                         f"Eliminó el objetivo semanal «{g['title']}» (semana del {g['week_start']})",
                         {"title": g["title"], "done": g["done"]})
            db.emit(c, "goal", "goal", g["id"], actor_id=user["id"])
            return {"ok": True}

    # -- fechas importantes y renovaciones ---------------------------------------------------
    @router.route("GET", "/api/dates", perm="planificacion.ver")
    def dates(req):
        return {"items": db.query("SELECT * FROM project_dates ORDER BY date")}

    @router.route("POST", "/api/dates", perm="planificacion.editar", idempotent=True)
    def new_date(req):
        data = req.json
        uid = clean_uid(data.get("uid"))
        title = clean_text(data.get("title"), "título", 200, required=True)
        d = clean_date(data.get("date"), "fecha")
        if not d:
            raise bad_request("Indica la fecha.")
        user = req.user
        pid = clean_int(data.get("project_id"), "proyecto", optional=True)
        with db.tx() as c:
            dup = c.execute("SELECT id FROM project_dates WHERE uid=?", (uid,)).fetchone()
            if dup:
                return {"id": dup[0], "duplicate": True}
            ts = now_iso()
            cur = c.execute("INSERT INTO project_dates(uid, project_id, title, date, description, created_by, created_at, "
                            "updated_at) VALUES (?,?,?,?,?,?,?,?)",
                            (uid, pid, title, d, clean_text(data.get("description"), "descripción", 2000), user["id"],
                             ts, ts))
            log_activity(db, c, user, "date", cur.lastrowid, d, "created", f"Añadió la fecha importante «{title}» ({d})",
                         project_id=pid)
            db.emit(c, "date", "date", cur.lastrowid, actor_id=user["id"])
            return Response.json({"id": cur.lastrowid}, 201)

    @router.route("DELETE", "/api/dates/{did}", perm="planificacion.editar", idempotent=True)
    def delete_date(req, did):
        user = req.user
        with db.tx() as c:
            row = c.execute("SELECT * FROM project_dates WHERE id=?", (did,)).fetchone()
            if not row:
                raise not_found("La fecha no existe.")
            c.execute("DELETE FROM project_dates WHERE id=?", (row["id"],))
            log_activity(db, c, user, "date", row["id"], row["date"], "deleted",
                         f"Eliminó la fecha importante «{row['title']}» ({row['date']})")
            db.emit(c, "date", "date", row["id"], actor_id=user["id"])
            return {"ok": True}

    @router.route("GET", "/api/renewals", perm="planificacion.ver")
    def renewals(req):
        un = names(db.conn)
        rows = db.query("SELECT * FROM renewals ORDER BY expires_on")
        today = date.today()
        for r in rows:
            r["owner_name"] = un.get(r["owner_id"]) if r["owner_id"] else None
            r["days_left"] = (date.fromisoformat(r["expires_on"]) - today).days
        return {"items": rows}

    @router.route("POST", "/api/renewals", perm="planificacion.editar", idempotent=True)
    def new_renewal(req):
        data = req.json
        uid = clean_uid(data.get("uid"))
        title = clean_text(data.get("title"), "título", 200, required=True)
        exp = clean_date(data.get("expires_on"), "fecha de caducidad")
        if not exp:
            raise bad_request("Indica la fecha de caducidad.")
        user = req.user
        with db.tx() as c:
            dup = c.execute("SELECT id FROM renewals WHERE uid=?", (uid,)).fetchone()
            if dup:
                return {"id": dup[0], "duplicate": True}
            ts = now_iso()
            cur = c.execute("INSERT INTO renewals(uid, project_id, title, expires_on, notes, owner_id, created_by, "
                            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                            (uid, clean_int(data.get("project_id"), "proyecto", optional=True), title, exp,
                             clean_text(data.get("notes"), "notas", 2000), user_exists(c, data.get("owner_id")),
                             user["id"], ts, ts))
            log_activity(db, c, user, "renewal", cur.lastrowid, title, "created",
                         f"Añadió la renovación «{title}» (caduca el {exp})")
            db.emit(c, "renewal", "renewal", cur.lastrowid, actor_id=user["id"])
            return Response.json({"id": cur.lastrowid}, 201)

    @router.route("PATCH", "/api/renewals/{rid}", perm="planificacion.editar", idempotent=True)
    def edit_renewal(req, rid):
        data = req.json
        user = req.user
        with db.tx() as c:
            row = c.execute("SELECT * FROM renewals WHERE id=?", (rid,)).fetchone()
            if not row:
                raise not_found("La renovación no existe.")
            sets, vals = [], []
            if "title" in data:
                sets.append("title=?")
                vals.append(clean_text(data["title"], "título", 200, required=True))
            if "notes" in data:
                sets.append("notes=?")
                vals.append(clean_text(data["notes"], "notas", 2000))
            if "owner_id" in data:
                sets.append("owner_id=?")
                vals.append(user_exists(c, data["owner_id"]))
            if "expires_on" in data:
                new_date = clean_date(data["expires_on"], "fecha de caducidad")
                sets.append("expires_on=?")
                vals.append(new_date)
                if data.get("renewed"):
                    sets.append("renewed_at=?")
                    vals.append(now_iso())
                log_activity(db, c, user, "renewal", row["id"], row["title"], "renewed" if data.get("renewed")
                             else "edited", f"{'Renovó' if data.get('renewed') else 'Cambió la caducidad de'} "
                                            f"«{row['title']}»: nueva fecha {new_date}")
            if sets:
                c.execute(f"UPDATE renewals SET {', '.join(sets)}, updated_at=? WHERE id=?", vals + [now_iso(), row["id"]])
            db.emit(c, "renewal", "renewal", row["id"], actor_id=user["id"])
        return {"ok": True}

    @router.route("DELETE", "/api/renewals/{rid}", perm="planificacion.editar", idempotent=True)
    def delete_renewal(req, rid):
        with db.tx() as c:
            row = c.execute("SELECT * FROM renewals WHERE id=?", (rid,)).fetchone()
            if not row:
                raise not_found("La renovación no existe.")
            c.execute("DELETE FROM renewals WHERE id=?", (row["id"],))
            log_activity(db, c, req.user, "renewal", row["id"], row["title"], "deleted",
                         f"Eliminó la renovación «{row['title']}»")
            db.emit(c, "renewal", "renewal", row["id"], actor_id=req.user["id"])
        return {"ok": True}

    # -- calendario ------------------------------------------------------------------------
    @router.route("GET", "/api/calendar", perm="planificacion.ver")
    def calendar(req):
        start = clean_date(req.q("from"), "desde")
        end = clean_date(req.q("to"), "hasta")
        if not start or not end:
            raise bad_request("Indica el rango de fechas del calendario.")
        today = date.today().isoformat()
        pclause, params = project_clause(db, req.user, "project_id", req)
        events = []
        for r in db.query(f"SELECT id, key, title, priority, status, target_date FROM incidents WHERE target_date "
                          f"BETWEEN ? AND ? AND {pclause}", [start, end] + params):
            events.append({"date": r["target_date"], "type": "incident", "id": r["id"], "key": r["key"],
                           "title": r["title"], "status": r["status"], "priority": r["priority"],
                           "overdue": r["status"] in ("pendiente", "en_progreso", "bloqueada") and r["target_date"] < today})
        for r in db.query(f"SELECT id, key, title, priority, status, target_date FROM tasks WHERE target_date "
                          f"BETWEEN ? AND ? AND {pclause}", [start, end] + params):
            events.append({"date": r["target_date"], "type": "task", "id": r["id"], "key": r["key"],
                           "title": r["title"], "status": r["status"], "priority": r["priority"],
                           "overdue": r["status"] != "resuelto" and r["target_date"] < today})
        for r in db.query("SELECT week_start, COUNT(*) n, SUM(done) d FROM goals WHERE week_start BETWEEN ? AND ? "
                          "GROUP BY week_start", (start, end)):
            events.append({"date": r["week_start"], "type": "goals",
                           "title": f"Objetivos semanales ({r['d'] or 0}/{r['n']})", "week": r["week_start"]})
        for r in db.query("SELECT * FROM project_dates WHERE date BETWEEN ? AND ?", (start, end)):
            events.append({"date": r["date"], "type": "date", "id": r["id"], "title": r["title"],
                           "description": r["description"]})
        for r in db.query(f"SELECT id, name, target_date, status FROM versions WHERE target_date BETWEEN ? AND ? AND "
                          f"{pclause}", [start, end] + params):
            events.append({"date": r["target_date"], "type": "version", "id": r["id"], "title": f"Versión {r['name']}",
                           "status": r["status"]})
        for r in db.query("SELECT id, title, expires_on FROM renewals WHERE expires_on BETWEEN ? AND ?", (start, end)):
            events.append({"date": r["expires_on"], "type": "renewal", "id": r["id"], "title": f"Caduca: {r['title']}"})
        un = names(db.conn)
        for r in db.query("SELECT * FROM absences WHERE end_date >= ? AND start_date <= ?", (start, end)):
            d = max(date.fromisoformat(r["start_date"]), date.fromisoformat(start))
            last = min(date.fromisoformat(r["end_date"]), date.fromisoformat(end))
            while d <= last:
                events.append({"date": d.isoformat(), "type": "absence", "id": r["id"],
                               "title": f"Ausencia: {un.get(r['user_id'], '?')}"})
                d += timedelta(days=1)
        for r in db.query("SELECT s.id, s.store, s.status, s.released_at, b.number FROM store_submissions s JOIN builds b "
                          "ON b.id = s.build_id WHERE s.released_at IS NOT NULL AND substr(s.released_at, 1, 10) "
                          "BETWEEN ? AND ?", (start, end)):
            events.append({"date": r["released_at"][:10], "type": "store", "id": r["id"],
                           "title": f"Publicada build {r['number']} ({r['store']})"})
        events.sort(key=lambda e: (e["date"], e["type"]))
        return {"events": events}

