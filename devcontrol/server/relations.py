"""Comentarios, dependencias, enlaces y datos relacionados de cualquier elemento."""
from __future__ import annotations

from . import auth
from .common import (
    INCIDENT_STATUSES,
    TASK_STATUSES,
    clean_text,
    clean_uid,
    log_activity,
    mentioned_users,
    names,
    notify,
)
from .db import now_iso
from .entities import TABLES, find_entity
from .web import HttpError, Response, bad_request, not_found

CLOSED = {"incident": ("resuelta", "duplicada"), "task": ("resuelto",)}
COMMENTABLE = {"incident", "task", "decision"}
LINKABLE = {"incident", "task", "decision", "idea", "wiki", "message", "build"}


def summary(db, entity_type: str, entity_id: int) -> dict | None:
    if entity_type == "message":
        m = db.query_one("SELECT m.id, m.body, m.channel_id, u.display_name FROM messages m JOIN users u "
                         "ON u.id = m.user_id WHERE m.id=?", (entity_id,))
        return {"type": "message", "id": m["id"], "title": f"{m['display_name']}: {m['body'][:120]}",
                "channel_id": m["channel_id"]} if m else None
    table = TABLES.get(entity_type)
    if not table:
        return None
    row = db.query_one(f"SELECT * FROM {table} WHERE id=?", (entity_id,))
    if not row:
        return None
    status = row.get("status")
    label = INCIDENT_STATUSES.get(status) if entity_type == "incident" else TASK_STATUSES.get(status) \
        if entity_type == "task" else status
    return {"type": entity_type, "id": row["id"], "key": row.get("key"), "project_id": row.get("project_id"),
            "title": row.get("title") or row.get("name") or row.get("number"), "status": status,
            "status_label": label, "closed": status in CLOSED.get(entity_type, ())}


def register(router, app):
    db = app.db

    def comment_perm(user, entity_type):
        if entity_type in ("incident", "task"):
            auth.require(user, "incidencias.comentar")
        else:
            auth.require(user, "conocimiento.editar")

    def involved(row: dict) -> list[int]:
        return [x for x in (row.get("created_by"), row.get("assignee_id")) if x]

    @router.route("POST", "/api/comments", idempotent=True)
    def add_comment(req):
        data = req.json
        user = req.user
        etype = data.get("entity_type") or "incident"
        if etype not in COMMENTABLE:
            raise bad_request("No se puede comentar en ese tipo de elemento.")
        comment_perm(user, etype)
        uid = clean_uid(data.get("uid"))
        body = clean_text(data.get("body"), "comentario", 20000, required=True)
        with db.tx() as c:
            row = find_entity(db, user, etype, data.get("entity") or data.get("entity_id") or "")
            existing = c.execute("SELECT id FROM comments WHERE uid=?", (uid,)).fetchone()
            if existing:
                return {"id": existing[0], "duplicate": True}
            ts = now_iso()
            cur = c.execute("INSERT INTO comments(uid, entity_type, entity_id, user_id, body, created_at) "
                            "VALUES (?,?,?,?,?,?)", (uid, etype, row["id"], user["id"], body, ts))
            table = TABLES[etype]
            if etype in ("incident", "task"):
                c.execute(f"UPDATE {table} SET updated_at=?, updated_by=? WHERE id=?", (ts, user["id"], row["id"]))
            if etype == "incident":
                app.mark_first_response(c, row, user)
            label = row.get("key") or row.get("title")
            log_activity(db, c, user, etype, row["id"], row.get("key"), "comment", f"Comentó en {label}",
                         {"comment_id": cur.lastrowid, "preview": body[:200]}, row.get("project_id"))
            mentioned = mentioned_users(c, body)
            notify(db, c, mentioned, "mention", f"{user['display_name']} te ha mencionado en {label}", body[:200],
                   f"mention-comment-{cur.lastrowid}", etype, row["id"], row.get("key"), exclude=user["id"])
            notify(db, c, [u for u in involved(row) if u not in mentioned], "comment",
                   f"{user['display_name']} comentó en {label}", body[:200], f"comment-{cur.lastrowid}", etype,
                   row["id"], row.get("key"), exclude=user["id"])
            db.emit(c, etype, etype, row["id"], actor_id=user["id"])
            return Response.json({"id": cur.lastrowid}, 201)

    @router.route("PATCH", "/api/comments/{cid}", idempotent=True)
    def edit_comment(req, cid):
        body = clean_text(req.json.get("body"), "comentario", 20000, required=True)
        user = req.user
        with db.tx() as c:
            cm = c.execute("SELECT * FROM comments WHERE id=?", (cid,)).fetchone()
            if not cm:
                raise not_found("El comentario no existe.")
            if cm["user_id"] != user["id"]:
                raise HttpError(403, "sin_permiso", "Solo puedes editar tus propios comentarios.")
            if cm["kind"] != "comment":
                raise bad_request("Este comentario forma parte del historial y no se puede editar.")
            if cm["body"] == body:
                return {"unchanged": True}
            ts = now_iso()
            c.execute("INSERT INTO edits(entity_type, entity_id, previous_body, edited_at, edited_by) "
                      "VALUES ('comment', ?, ?, ?, ?)", (cm["id"], cm["body"], ts, user["id"]))
            c.execute("UPDATE comments SET body=?, edited_at=? WHERE id=?", (body, ts, cm["id"]))
            log_activity(db, c, user, cm["entity_type"], cm["entity_id"], None, "comment_edit", "Editó un comentario",
                         {"comment_id": cm["id"]})
            db.emit(c, cm["entity_type"], cm["entity_type"], cm["entity_id"], actor_id=user["id"])
            return {"ok": True}

    # -- dependencias ----------------------------------------------------------------
    def is_closed(c, etype, eid) -> bool:
        row = c.execute(f"SELECT status FROM {TABLES[etype]} WHERE id=?", (eid,)).fetchone()
        return bool(row) and row[0] in CLOSED.get(etype, ())

    @router.route("POST", "/api/dependencies", idempotent=True)
    def add_dependency(req):
        data = req.json
        user = req.user
        auth.require_any(user, "tareas.trabajar", "tareas.editar", "incidencias.trabajar")
        bt, dt = data.get("blocker_type"), data.get("blocked_type")
        if bt not in CLOSED or dt not in CLOSED:
            raise bad_request("Las dependencias se crean entre tareas e incidencias.")
        with db.tx() as c:
            blocker = find_entity(db, user, bt, data.get("blocker") or "")
            blocked = find_entity(db, user, dt, data.get("blocked") or "")
            if (bt, blocker["id"]) == (dt, blocked["id"]):
                raise bad_request("Un elemento no puede depender de sí mismo.")
            # Evita ciclos: si B ya depende (directa o indirectamente) de A, A no puede depender de B.
            seen, frontier = set(), [(bt, blocker["id"])]
            while frontier:
                cur = frontier.pop()
                if cur == (dt, blocked["id"]):
                    raise bad_request("Esa dependencia crearía un ciclo (los dos elementos se bloquearían entre sí).")
                if cur in seen:
                    continue
                seen.add(cur)
                frontier += [(r[0], r[1]) for r in c.execute(
                    "SELECT blocker_type, blocker_id FROM dependencies WHERE blocked_type=? AND blocked_id=?", cur)]
            cur = c.execute("INSERT OR IGNORE INTO dependencies(blocker_type, blocker_id, blocked_type, blocked_id, "
                            "created_by, created_at) VALUES (?,?,?,?,?,?)",
                            (bt, blocker["id"], dt, blocked["id"], user["id"], now_iso()))
            if not cur.rowcount:
                return {"duplicate": True}
            log_activity(db, c, user, dt, blocked["id"], blocked["key"], "dependency_added",
                         f"{blocked['key']} queda bloqueada por {blocker['key']}", project_id=blocked["project_id"])
            for et, eid in ((bt, blocker["id"]), (dt, blocked["id"])):
                db.emit(c, et, et, eid, actor_id=user["id"])
            return Response.json({"id": cur.lastrowid}, 201)

    @router.route("DELETE", "/api/dependencies/{did}", idempotent=True)
    def delete_dependency(req, did):
        user = req.user
        auth.require_any(user, "tareas.trabajar", "tareas.editar", "incidencias.trabajar")
        with db.tx() as c:
            d = c.execute("SELECT * FROM dependencies WHERE id=?", (did,)).fetchone()
            if not d:
                raise not_found("La dependencia no existe.")
            blocked = find_entity(db, user, d["blocked_type"], d["blocked_id"])
            blocker = summary(db, d["blocker_type"], d["blocker_id"]) or {}
            c.execute("DELETE FROM dependencies WHERE id=?", (did,))
            log_activity(db, c, user, d["blocked_type"], d["blocked_id"], blocked["key"], "dependency_removed",
                         f"Quitó la dependencia de {blocked['key']} con {blocker.get('key', '?')}",
                         project_id=blocked["project_id"])
            db.emit(c, d["blocked_type"], d["blocked_type"], d["blocked_id"], actor_id=user["id"])
            db.emit(c, d["blocker_type"], d["blocker_type"], d["blocker_id"], actor_id=user["id"])
            return {"ok": True}

    def unblock_dependents(c, etype, eid, key, user):
        """Al cerrarse un elemento, avisa a quien estaba esperando por él."""
        for d in c.execute("SELECT * FROM dependencies WHERE blocker_type=? AND blocker_id=?", (etype, eid)).fetchall():
            pending = [r for r in c.execute("SELECT blocker_type, blocker_id FROM dependencies WHERE blocked_type=? "
                                            "AND blocked_id=?", (d["blocked_type"], d["blocked_id"]))
                       if not (r[0] == etype and r[1] == eid) and not is_closed(c, r[0], r[1])]
            if pending:
                continue
            row = c.execute(f"SELECT * FROM {TABLES[d['blocked_type']]} WHERE id=?", (d["blocked_id"],)).fetchone()
            if not row:
                continue
            target = row["assignee_id"] or row["created_by"]
            notify(db, c, [target], "dependency_unblocked", f"Ya puedes empezar {row['key']}",
                   f"{key} se ha cerrado y {row['key']} ({row['title']}) ya no está bloqueada.",
                   f"unblocked-{d['blocked_type']}-{row['id']}-{etype}-{eid}", d["blocked_type"], row["id"],
                   row["key"])
            db.emit(c, d["blocked_type"], d["blocked_type"], row["id"], actor_id=user["id"] if user else None)

    app.unblock_dependents = unblock_dependents

    def open_blockers(etype, eid) -> list[dict]:
        out = []
        for r in db.query("SELECT blocker_type, blocker_id FROM dependencies WHERE blocked_type=? AND blocked_id=?",
                          (etype, eid)):
            s = summary(db, r["blocker_type"], r["blocker_id"])
            if s and not s["closed"]:
                out.append(s)
        return out

    app.open_blockers = open_blockers

    # -- enlaces genéricos ------------------------------------------------------------
    @router.route("POST", "/api/links", idempotent=True)
    def add_link(req):
        data = req.json
        user = req.user
        ft, tt = data.get("from_type"), data.get("to_type")
        if ft not in LINKABLE or tt not in LINKABLE:
            raise bad_request("Tipo de elemento no válido para enlazar.")
        kind = clean_text(data.get("kind") or "relacionado", "tipo de enlace", 30)
        with db.tx() as c:
            src = find_entity(db, user, ft, data.get("from")) if ft != "message" else \
                c.execute("SELECT id FROM messages WHERE id=?", (data.get("from"),)).fetchone()
            dst = find_entity(db, user, tt, data.get("to")) if tt != "message" else \
                c.execute("SELECT id FROM messages WHERE id=?", (data.get("to"),)).fetchone()
            if not src or not dst:
                raise not_found("Uno de los elementos no existe.")
            c.execute("INSERT OR IGNORE INTO links(from_type, from_id, to_type, to_id, kind, created_by, created_at) "
                      "VALUES (?,?,?,?,?,?,?)", (ft, src["id"], tt, dst["id"], kind, user["id"], now_iso()))
            db.emit(c, ft, ft, src["id"], actor_id=user["id"])
            db.emit(c, tt, tt, dst["id"], actor_id=user["id"])
        return Response.json({"ok": True}, 201)

    @router.route("DELETE", "/api/links/{lid}", idempotent=True)
    def delete_link(req, lid):
        with db.tx() as c:
            link = c.execute("SELECT * FROM links WHERE id=?", (lid,)).fetchone()
            if not link:
                raise not_found("El enlace no existe.")
            c.execute("DELETE FROM links WHERE id=?", (lid,))
            db.emit(c, link["from_type"], link["from_type"], link["from_id"], actor_id=req.user["id"])
            db.emit(c, link["to_type"], link["to_type"], link["to_id"], actor_id=req.user["id"])
        return {"ok": True}

    # -- datos relacionados para las fichas --------------------------------------------
    def entity_extras(etype: str, eid: int, user: dict) -> dict:
        un = names(db.conn)
        comments = db.query("SELECT * FROM comments WHERE entity_type=? AND entity_id=? ORDER BY id", (etype, eid))
        for cm in comments:
            cm["user_name"] = un.get(cm["user_id"])
            cm["edits"] = db.query("SELECT previous_body, edited_at FROM edits WHERE entity_type='comment' AND "
                                   "entity_id=? ORDER BY id", (cm["id"],))
        history = db.query("SELECT * FROM activity WHERE entity_type=? AND entity_id=? ORDER BY id", (etype, eid))
        for h in history:
            h["user_name"] = un.get(h["user_id"], "Sistema")
        blocked_by, blocks = [], []
        for d in db.query("SELECT * FROM dependencies WHERE (blocked_type=? AND blocked_id=?) OR "
                          "(blocker_type=? AND blocker_id=?)", (etype, eid, etype, eid)):
            if d["blocked_type"] == etype and d["blocked_id"] == eid:
                s = summary(db, d["blocker_type"], d["blocker_id"])
                if s:
                    blocked_by.append({**s, "dependency_id": d["id"]})
            else:
                s = summary(db, d["blocked_type"], d["blocked_id"])
                if s:
                    blocks.append({**s, "dependency_id": d["id"]})
        links = []
        for lk in db.query("SELECT * FROM links WHERE (from_type=? AND from_id=?) OR (to_type=? AND to_id=?)",
                           (etype, eid, etype, eid)):
            other = (lk["to_type"], lk["to_id"]) if (lk["from_type"], lk["from_id"]) == (etype, eid) else \
                (lk["from_type"], lk["from_id"])
            s = summary(db, *other)
            if s:
                links.append({**s, "link_id": lk["id"], "kind": lk["kind"]})
        code = db.query("SELECT * FROM code_links WHERE entity_type=? AND entity_id=? ORDER BY created_at DESC",
                        (etype, eid))
        time_rows = db.query("SELECT user_id, SUM(COALESCE(seconds, 0)) s FROM time_entries WHERE entity_type=? AND "
                             "entity_id=? GROUP BY user_id", (etype, eid))
        running = db.query("SELECT user_id, started_at FROM time_entries WHERE entity_type=? AND entity_id=? AND "
                           "ended_at IS NULL", (etype, eid))
        att_type = {"incident": "incident", "task": "task"}.get(etype)
        return {
            "comments": comments, "history": history, "blocked_by": blocked_by, "blocks": blocks, "links": links,
            "code_links": code, "attachments": app.attachments.list_for(att_type, eid) if att_type else [],
            "time": {"total_seconds": sum(r["s"] or 0 for r in time_rows),
                     "by_user": [{"user_id": r["user_id"], "user_name": un.get(r["user_id"]), "seconds": r["s"]}
                                 for r in time_rows],
                     "running": [{"user_id": r["user_id"], "user_name": un.get(r["user_id"]),
                                  "started_at": r["started_at"]} for r in running]},
        }

    app.entity_extras = entity_extras
