"""Conocimiento: registro de decisiones, wiki y bóveda de credenciales cifrada."""
from __future__ import annotations

import base64
import hashlib
import json
import os

from . import auth
from .common import (
    DECISION_STATUSES,
    check_version,
    clean_choice,
    clean_int,
    clean_text,
    clean_uid,
    default_project_id,
    log_activity,
    names,
    notify,
    project_clause,
    require_project,
    users_with_perm,
    visible_project_ids,
)
from .db import now_iso
from .entities import find_entity
from .web import HttpError, Response, bad_request, forbidden, not_found

VAULT_CHECK = b"DEV CONTROL vault v1"


def register(router, app):
    db = app.db

    # -- decisiones ---------------------------------------------------------------------
    def ser_decision(r, un, pm):
        r["status_label"] = DECISION_STATUSES.get(r["status"])
        r["created_by_name"] = un.get(r["created_by"])
        r["project_key"] = (pm.get(r["project_id"]) or {}).get("key")
        r["version"] = r["row_version"]
        if r.get("superseded_by"):
            s = db.query_one("SELECT key, title FROM decisions WHERE id=?", (r["superseded_by"],))
            r["superseded_by_key"] = s["key"] if s else None
        return r

    def pm():
        return {p["id"]: p for p in db.query("SELECT id, key FROM projects")}

    @router.route("GET", "/api/decisions", perm="conocimiento.ver")
    def decisions(req):
        pclause, params = project_clause(db, req.user, "project_id", req)
        where = [pclause]
        if req.q("status"):
            where.append("status = ?")
            params.append(clean_choice(req.q("status"), "estado", DECISION_STATUSES))
        if req.q("q"):
            like = f"%{req.q('q')}%"
            where.append("(title LIKE ? OR context LIKE ? OR decision LIKE ? OR key LIKE ?)")
            params += [like] * 4
        rows = db.query("SELECT * FROM decisions WHERE " + " AND ".join(where) + " ORDER BY number DESC", params)
        un, p = names(db.conn), pm()
        return {"items": [ser_decision(r, un, p) for r in rows]}

    @router.route("GET", "/api/decisions/{did}", perm="conocimiento.ver")
    def decision_detail(req, did):
        row = find_entity(db, req.user, "decision", did)
        extras = app.entity_extras("decision", row["id"], req.user)
        src = None
        if row["source_message_id"]:
            from .relations import summary
            src = summary(db, "message", row["source_message_id"])
        return {"item": {**ser_decision(row, names(db.conn), pm()), **extras, "source_message": src}}

    SPEC = {
        "title": lambda v: clean_text(v, "título", 200, required=True),
        "context": lambda v: clean_text(v, "contexto", 20000),
        "options": lambda v: clean_text(v, "opciones valoradas", 20000),
        "decision": lambda v: clean_text(v, "decisión", 20000),
        "status": lambda v: clean_choice(v, "estado", DECISION_STATUSES, "propuesta"),
    }

    @router.route("POST", "/api/decisions", perm="conocimiento.editar", idempotent=True)
    def new_decision(req):
        data = req.json
        user = req.user
        project = require_project(db, user, data.get("project_id") or default_project_id(db, user))
        f = {k: fn(data.get(k)) for k, fn in SPEC.items()}
        with db.tx() as c:
            uid = clean_uid(data.get("uid"))
            dup = c.execute("SELECT id FROM decisions WHERE uid=?", (uid,)).fetchone()
            if dup:
                return {"id": dup[0], "duplicate": True}
            number = db.next_counter(c, "decision")
            key = f"DEC-{number:03d}"
            ts = now_iso()
            src = clean_int(data.get("source_message_id"), "mensaje", optional=True)
            cur = c.execute("INSERT INTO decisions(uid, project_id, number, key, title, context, options, decision, status, "
                            "source_message_id, created_by, decided_at, created_at, updated_at) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (uid, project["id"], number, key, f["title"], f["context"], f["options"], f["decision"],
                             f["status"], src, user["id"], ts if f["status"] == "aceptada" else None, ts, ts))
            did = cur.lastrowid
            if src and c.execute("SELECT 1 FROM messages WHERE id=?", (src,)).fetchone():
                c.execute("INSERT OR IGNORE INTO links(from_type, from_id, to_type, to_id, kind, created_by, created_at) "
                          "VALUES ('message', ?, 'decision', ?, 'origen', ?, ?)", (src, did, user["id"], ts))
                db.emit(c, "message", "message", src, actor_id=user["id"])
            log_activity(db, c, user, "decision", did, key, "created", f"Registró la decisión {key}: {f['title']}",
                         project_id=project["id"])
            db.index(c, "decision", did, project["id"], key, f["title"], f"{f['context']} {f['decision']}")
            db.emit(c, "decision", "decision", did, actor_id=user["id"])
            return Response.json({"id": did, "key": key}, 201)

    @router.route("PATCH", "/api/decisions/{did}", perm="conocimiento.editar", idempotent=True)
    def edit_decision(req, did):
        data = req.json
        user = req.user
        with db.tx() as c:
            row = find_entity(db, user, "decision", did)
            check_version(row, data, f"La decisión {row['key']}")
            f = {k: fn(data[k]) for k, fn in SPEC.items() if k in data}
            if "superseded_by" in data and data["superseded_by"]:
                other = find_entity(db, user, "decision", data["superseded_by"])
                if other["id"] == row["id"]:
                    raise bad_request("Una decisión no puede sustituirse a sí misma.")
                f["superseded_by"] = other["id"]
                f["status"] = "sustituida"
            changed = {k: v for k, v in f.items() if row.get(k) != v}
            if not changed:
                return {"unchanged": True}
            if changed.get("status") == "aceptada":
                changed["decided_at"] = now_iso()
            sets = ", ".join(f"{k}=?" for k in changed)
            c.execute(f"UPDATE decisions SET {sets}, updated_at=?, row_version=row_version+1 WHERE id=?",
                      list(changed.values()) + [now_iso(), row["id"]])
            for k in changed:
                if k in ("context", "options", "decision"):
                    c.execute("INSERT INTO edits(entity_type, entity_id, previous_body, edited_at, edited_by) VALUES "
                              "('decision', ?, ?, ?, ?)", (row["id"], f"[{k}]\n{row[k]}", now_iso(), user["id"]))
            extra = f" (estado: {DECISION_STATUSES[changed['status']]})" if "status" in changed else ""
            log_activity(db, c, user, "decision", row["id"], row["key"], "edited",
                         f"Actualizó la decisión {row['key']}{extra}", project_id=row["project_id"])
            new = c.execute("SELECT * FROM decisions WHERE id=?", (row["id"],)).fetchone()
            db.index(c, "decision", row["id"], row["project_id"], row["key"], new["title"],
                     f"{new['context']} {new['decision']}")
            db.emit(c, "decision", "decision", row["id"], actor_id=user["id"])
            return {"ok": True}

    # -- wiki -----------------------------------------------------------------------------
    def wiki_visible(row, user) -> bool:
        ids = visible_project_ids(db, user)
        return row["project_id"] is None or ids is None or row["project_id"] in ids

    @router.route("GET", "/api/wiki", perm="conocimiento.ver")
    def wiki_list(req):
        un = names(db.conn)
        rows = [r for r in db.query("SELECT id, uid, project_id, folder, title, updated_at, updated_by, "
                                    "substr(body, 1, 200) AS excerpt FROM wiki_pages ORDER BY folder, title")
                if wiki_visible(r, req.user)]
        for r in rows:
            r["updated_by_name"] = un.get(r["updated_by"])
        return {"items": rows}

    @router.route("GET", "/api/wiki/{pid}", perm="conocimiento.ver")
    def wiki_page(req, pid):
        row = db.query_one("SELECT * FROM wiki_pages WHERE id=?", (pid,))
        if not row or not wiki_visible(row, req.user):
            raise not_found("La página no existe.")
        un = names(db.conn)
        revs = db.query("SELECT id, title, edited_by, edited_at, length(body) AS size FROM wiki_revisions WHERE page_id=? "
                        "ORDER BY id DESC", (row["id"],))
        for r in revs:
            r["edited_by_name"] = un.get(r["edited_by"])
        row["revisions"] = revs
        row["version"] = row["row_version"]
        row["created_by_name"] = un.get(row["created_by"])
        row["updated_by_name"] = un.get(row["updated_by"])
        row["attachments"] = app.attachments.list_for("wiki", row["id"])
        if row["source_incident_id"]:
            row["source_incident"] = db.query_one("SELECT id, key, title FROM incidents WHERE id=?",
                                                  (row["source_incident_id"],))
        return {"item": row}

    @router.route("GET", "/api/wiki/{pid}/revisions/{rid}", perm="conocimiento.ver")
    def wiki_revision(req, pid, rid):
        row = db.query_one("SELECT * FROM wiki_revisions WHERE id=? AND page_id=?", (rid, pid))
        if not row:
            raise not_found("La versión no existe.")
        return {"item": row}

    def create_page(c, user, data, source_incident_id=None) -> int:
        uid = clean_uid(data.get("uid"))
        dup = c.execute("SELECT id FROM wiki_pages WHERE uid=?", (uid,)).fetchone()
        if dup:
            return dup[0]
        pid = clean_int(data.get("project_id"), "proyecto", optional=True)
        if pid:
            require_project(db, user, pid)
        title = clean_text(data.get("title"), "título", 200, required=True)
        body = clean_text(data.get("body"), "contenido", 200000)
        ts = now_iso()
        cur = c.execute("INSERT INTO wiki_pages(uid, project_id, folder, title, body, source_incident_id, created_by, "
                        "updated_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (uid, pid, clean_text(data.get("folder"), "carpeta", 120), title, body, source_incident_id,
                         user["id"], user["id"], ts, ts))
        log_activity(db, c, user, "wiki", cur.lastrowid, title, "created", f"Creó la página de la wiki «{title}»",
                     project_id=pid)
        db.index(c, "wiki", cur.lastrowid, pid, "", title, body)
        db.emit(c, "wiki", "wiki", cur.lastrowid, actor_id=user["id"])
        return cur.lastrowid

    @router.route("POST", "/api/wiki", perm="conocimiento.editar", idempotent=True)
    def wiki_new(req):
        with db.tx() as c:
            pid = create_page(c, req.user, req.json)
        return Response.json({"id": pid}, 201)

    @router.route("PATCH", "/api/wiki/{pid}", perm="conocimiento.editar", idempotent=True)
    def wiki_edit(req, pid):
        data = req.json
        user = req.user
        with db.tx() as c:
            row = c.execute("SELECT * FROM wiki_pages WHERE id=?", (pid,)).fetchone()
            if not row or not wiki_visible(dict(row), user):
                raise not_found("La página no existe.")
            row = dict(row)
            check_version(row, data, "La página")
            title = clean_text(data.get("title", row["title"]), "título", 200, required=True)
            body = clean_text(data.get("body", row["body"]), "contenido", 200000)
            folder = clean_text(data.get("folder", row["folder"]), "carpeta", 120)
            if (title, body, folder) == (row["title"], row["body"], row["folder"]):
                return {"unchanged": True}
            ts = now_iso()
            c.execute("INSERT INTO wiki_revisions(page_id, title, body, edited_by, edited_at) VALUES (?,?,?,?,?)",
                      (row["id"], row["title"], row["body"], row["updated_by"], row["updated_at"]))
            c.execute("UPDATE wiki_pages SET title=?, body=?, folder=?, updated_by=?, updated_at=?, "
                      "row_version=row_version+1 WHERE id=?", (title, body, folder, user["id"], ts, row["id"]))
            log_activity(db, c, user, "wiki", row["id"], title, "edited", f"Editó la página de la wiki «{title}»",
                         project_id=row["project_id"])
            db.index(c, "wiki", row["id"], row["project_id"], "", title, body)
            db.emit(c, "wiki", "wiki", row["id"], actor_id=user["id"])
        return {"ok": True}

    @router.route("POST", "/api/incidents/{ident}/to-wiki", perm="conocimiento.editar", idempotent=True)
    def incident_to_wiki(req, ident):
        """Convierte la solución de una incidencia en un artículo de la wiki."""
        user = req.user
        with db.tx() as c:
            inc = find_entity(db, user, "incident", ident)
            res = c.execute("SELECT * FROM resolutions WHERE entity_type='incident' AND entity_id=? AND "
                            "state='verificada' ORDER BY id DESC LIMIT 1", (inc["id"],)).fetchone()
            if not res:
                raise bad_request("La incidencia todavía no tiene una solución verificada.")
            body = (f"## Problema\n{inc['description'] or inc['title']}\n\n"
                    + (f"## Cómo reproducirlo\n{inc['steps']}\n\n" if inc["steps"] else "")
                    + (f"## Resultado observado\n{inc['observed']}\n\n" if inc["observed"] else "")
                    + f"## Causa\n{req.json.get('cause') or '(completa la causa)'}\n\n"
                    + f"## Solución\n{res['solution'] or '(sin documentar)'}\n\n"
                    + f"_Origen: {inc['key']}_\n")
            pid = create_page(c, user, {"uid": clean_uid(req.json.get("uid")), "title": f"{inc['title']}",
                                        "body": body, "folder": "Soluciones", "project_id": inc["project_id"]},
                              source_incident_id=inc["id"])
            c.execute("INSERT OR IGNORE INTO links(from_type, from_id, to_type, to_id, kind, created_by, created_at) "
                      "VALUES ('incident', ?, 'wiki', ?, 'articulo', ?, ?)", (inc["id"], pid, user["id"], now_iso()))
            db.emit(c, "incident", "incident", inc["id"], actor_id=user["id"])
        return Response.json({"id": pid}, 201)

    # -- bóveda --------------------------------------------------------------------------
    def crypto():
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            return AESGCM
        except ImportError:
            raise HttpError(501, "boveda_no_disponible", "La bóveda necesita el componente de cifrado, incluido en el "
                                                         "instalador de DEV CONTROL.") from None

    def derive(passphrase: str, salt: bytes) -> bytes:
        return hashlib.scrypt(passphrase.encode("utf-8"), salt=salt, n=2 ** 15, r=8, p=1, maxmem=64 * 1024 * 1024,
                              dklen=32)

    def encrypt(key: bytes, text: str) -> str:
        if not text:
            return ""
        nonce = os.urandom(12)
        return base64.b64encode(nonce + crypto()(key).encrypt(nonce, text.encode("utf-8"), None)).decode()

    def decrypt(key: bytes, blob: str) -> str:
        if not blob:
            return ""
        raw = base64.b64decode(blob)
        return crypto()(key).decrypt(raw[:12], raw[12:], None).decode("utf-8")

    def vault_state() -> dict:
        cfg = db.get_setting("vault") or {}
        return {"configured": bool(cfg.get("salt")), "unlocked": app.vault_key is not None}

    def require_unlocked() -> bytes:
        if app.vault_key is None:
            raise HttpError(423, "boveda_bloqueada", "La bóveda está bloqueada. Un administrador debe desbloquearla "
                                                     "con la frase maestra.")
        return app.vault_key

    def vlog(c, item_id, user, action):
        c.execute("INSERT INTO vault_log(item_id, user_id, action, at) VALUES (?,?,?,?)",
                  (item_id, user["id"], action, now_iso()))

    @router.route("GET", "/api/vault", perm="boveda.ver")
    def vault_status(req):
        return vault_state()

    @router.route("POST", "/api/vault/setup", perm="boveda.gestionar")
    def vault_setup(req):
        if vault_state()["configured"]:
            raise bad_request("La bóveda ya está configurada.")
        phrase = req.json.get("passphrase") or ""
        if len(phrase) < 12:
            raise bad_request("La frase maestra debe tener al menos 12 caracteres.")
        crypto()
        salt = os.urandom(16)
        key = derive(phrase, salt)
        check = encrypt(key, VAULT_CHECK.decode())
        with db.tx() as c:
            db.put_setting(c, "vault", {"salt": base64.b64encode(salt).decode(), "check": check, "created_at": now_iso()})
            vlog(c, None, req.user, "configurar")
            log_activity(db, c, req.user, "vault", None, None, "setup", "Configuró la bóveda de credenciales")
        app.vault_key = key
        return vault_state()

    @router.route("POST", "/api/vault/unlock", perm="boveda.gestionar")
    def vault_unlock(req):
        cfg = db.get_setting("vault") or {}
        if not cfg.get("salt"):
            raise bad_request("La bóveda todavía no está configurada.")
        app.throttle.check(f"vault:{req.user['id']}")
        key = derive(req.json.get("passphrase") or "", base64.b64decode(cfg["salt"]))
        try:
            ok = decrypt(key, cfg["check"]) == VAULT_CHECK.decode()
        except Exception:
            ok = False
        if not ok:
            app.throttle.fail(f"vault:{req.user['id']}")
            with db.tx() as c:
                vlog(c, None, req.user, "frase incorrecta")
            raise HttpError(400, "frase_incorrecta", "La frase maestra no es correcta.")
        app.vault_key = key
        with db.tx() as c:
            vlog(c, None, req.user, "desbloquear")
        return vault_state()

    @router.route("POST", "/api/vault/lock", perm="boveda.gestionar")
    def vault_lock(req):
        app.vault_key = None
        with db.tx() as c:
            vlog(c, None, req.user, "bloquear")
        return vault_state()

    def can_see(item, user) -> bool:
        if auth.has_perm(user, "boveda.gestionar"):
            return True
        allowed = json.loads(item["allowed_roles"] or "[]")
        if user["role_key"] not in allowed:
            return False
        ids = visible_project_ids(db, user)
        return item["project_id"] is None or ids is None or item["project_id"] in ids

    @router.route("GET", "/api/vault/items", perm="boveda.ver")
    def vault_items(req):
        un = names(db.conn)
        items = []
        for r in db.query("SELECT id, uid, project_id, name, username, url, allowed_roles, created_by, created_at, "
                          "updated_at FROM vault_items ORDER BY name"):
            if can_see(r, req.user):
                r["allowed_roles"] = json.loads(r["allowed_roles"] or "[]")
                r["created_by_name"] = un.get(r["created_by"])
                items.append(r)
        return {"items": items, **vault_state()}

    def clean_roles(value) -> str:
        keys = {r["key"] for r in db.query("SELECT key FROM roles")}
        roles = [r for r in (value or []) if r in keys]
        return json.dumps(roles)

    @router.route("POST", "/api/vault/items", perm="boveda.gestionar", idempotent=True)
    def vault_new(req):
        key = require_unlocked()
        data = req.json
        with db.tx() as c:
            uid = clean_uid(data.get("uid"))
            dup = c.execute("SELECT id FROM vault_items WHERE uid=?", (uid,)).fetchone()
            if dup:
                return {"id": dup[0], "duplicate": True}
            ts = now_iso()
            cur = c.execute("INSERT INTO vault_items(uid, project_id, name, username, url, secret_enc, notes_enc, "
                            "allowed_roles, created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (uid, clean_int(data.get("project_id"), "proyecto", optional=True),
                             clean_text(data.get("name"), "nombre", 120, required=True),
                             clean_text(data.get("username"), "usuario", 200), clean_text(data.get("url"), "URL", 500),
                             encrypt(key, clean_text(data.get("secret"), "secreto", 10000, required=True)),
                             encrypt(key, clean_text(data.get("notes"), "notas", 10000)),
                             clean_roles(data.get("allowed_roles")), req.user["id"], ts, ts))
            vlog(c, cur.lastrowid, req.user, "crear")
            log_activity(db, c, req.user, "vault", cur.lastrowid, None, "created",
                         f"Añadió el secreto «{data.get('name')}» a la bóveda")
            db.emit(c, "vault", "vault", cur.lastrowid, actor_id=req.user["id"])
            return Response.json({"id": cur.lastrowid}, 201)

    @router.route("PATCH", "/api/vault/items/{vid}", perm="boveda.gestionar", idempotent=True)
    def vault_edit(req, vid):
        data = req.json
        with db.tx() as c:
            row = c.execute("SELECT * FROM vault_items WHERE id=?", (vid,)).fetchone()
            if not row:
                raise not_found("El secreto no existe.")
            sets, vals = [], []
            for k, n in (("name", 120), ("username", 200), ("url", 500)):
                if k in data:
                    sets.append(f"{k}=?")
                    vals.append(clean_text(data[k], k, n, required=k == "name"))
            if "allowed_roles" in data:
                sets.append("allowed_roles=?")
                vals.append(clean_roles(data["allowed_roles"]))
            if data.get("secret") or "notes" in data:
                key = require_unlocked()
                if data.get("secret"):
                    sets.append("secret_enc=?")
                    vals.append(encrypt(key, clean_text(data["secret"], "secreto", 10000, required=True)))
                if "notes" in data:
                    sets.append("notes_enc=?")
                    vals.append(encrypt(key, clean_text(data["notes"], "notas", 10000)))
            if sets:
                c.execute(f"UPDATE vault_items SET {', '.join(sets)}, updated_at=? WHERE id=?", vals + [now_iso(), row["id"]])
                vlog(c, row["id"], req.user, "editar")
            db.emit(c, "vault", "vault", row["id"], actor_id=req.user["id"])
        return {"ok": True}

    @router.route("DELETE", "/api/vault/items/{vid}", perm="boveda.gestionar", idempotent=True)
    def vault_delete(req, vid):
        with db.tx() as c:
            row = c.execute("SELECT * FROM vault_items WHERE id=?", (vid,)).fetchone()
            if not row:
                raise not_found("El secreto no existe.")
            c.execute("DELETE FROM vault_items WHERE id=?", (row["id"],))
            vlog(c, row["id"], req.user, f"eliminar «{row['name']}»")
            log_activity(db, c, req.user, "vault", row["id"], None, "deleted", f"Eliminó el secreto «{row['name']}»")
            db.emit(c, "vault", "vault", row["id"], actor_id=req.user["id"])
        return {"ok": True}

    @router.route("POST", "/api/vault/items/{vid}/reveal", perm="boveda.ver")
    def vault_reveal(req, vid):
        row = db.query_one("SELECT * FROM vault_items WHERE id=?", (vid,))
        if not row or not can_see(row, req.user):
            with db.tx() as c:
                vlog(c, int(vid) if str(vid).isdigit() else None, req.user, "acceso denegado")
            raise forbidden("No tienes acceso a este secreto.")
        key = require_unlocked()
        with db.tx() as c:
            vlog(c, row["id"], req.user, "ver")
            if row["created_by"] != req.user["id"]:
                notify(db, c, users_with_perm(c, "boveda.gestionar"), "system",
                       f"{req.user['display_name']} ha consultado «{row['name']}»", "Bóveda de credenciales",
                       f"vault-view-{row['id']}-{req.user['id']}-{now_iso()[:13]}", exclude=req.user["id"])
        return {"secret": decrypt(key, row["secret_enc"]), "notes": decrypt(key, row["notes_enc"])}

    @router.route("GET", "/api/vault/log", perm="boveda.gestionar")
    def vault_log(req):
        un = names(db.conn)
        rows = db.query("SELECT l.*, v.name AS item_name FROM vault_log l LEFT JOIN vault_items v ON v.id = l.item_id "
                        "ORDER BY l.id DESC LIMIT 300")
        for r in rows:
            r["user_name"] = un.get(r["user_id"])
        return {"items": rows}
