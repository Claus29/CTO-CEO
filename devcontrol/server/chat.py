"""Chat del equipo: canales, respuestas, menciones, enlaces a trabajo y recordatorios."""
from __future__ import annotations

from datetime import datetime, timezone

from . import auth
from .common import clean_int, clean_text, clean_uid, log_activity, mentioned_users, names, notify, visible_project_ids
from .db import now_iso, parse_iso
from .entities import find_entity
from .relations import summary
from .web import HttpError, Response, bad_request, not_found


def register(router, app):
    db = app.db

    def channel_ids_for(user) -> list[int]:
        ids = visible_project_ids(db, user)
        rows = db.query("SELECT id, project_id FROM channels WHERE archived=0")
        return [r["id"] for r in rows if r["project_id"] is None or ids is None or r["project_id"] in ids]

    def get_channel(user, cid) -> dict:
        try:
            cid = int(cid)
        except (TypeError, ValueError):
            raise bad_request("Canal no válido.") from None
        if cid not in channel_ids_for(user):
            raise not_found("El canal no existe o no tienes acceso.")
        return db.query_one("SELECT * FROM channels WHERE id=?", (cid,))

    def general_channel_id() -> int:
        row = db.query_one("SELECT id FROM channels WHERE kind='general' ORDER BY id LIMIT 1")
        if row:
            return row["id"]
        with db.tx() as c:
            return c.execute("INSERT INTO channels(uid, name, kind, created_at) VALUES (?, 'General', 'general', ?)",
                             (app.new_uid(), now_iso())).lastrowid

    app.general_channel_id = general_channel_id

    def unread_by_channel(user_id: int, channel_ids: list[int]) -> dict[int, int]:
        if not channel_ids:
            return {}
        marks = ",".join("?" * len(channel_ids))
        state = {r["channel_id"]: r["last_read_id"] for r in db.query(
            f"SELECT channel_id, last_read_id FROM chat_state WHERE user_id=? AND channel_id IN ({marks})",
            [user_id] + channel_ids)}
        out = {}
        for r in db.query(f"SELECT channel_id, id FROM messages WHERE user_id<>? AND channel_id IN ({marks})",
                          [user_id] + channel_ids):
            if r["id"] > state.get(r["channel_id"], 0):
                out[r["channel_id"]] = out.get(r["channel_id"], 0) + 1
        return out

    def unread_total(user) -> int:
        return sum(unread_by_channel(user["id"], channel_ids_for(user)).values())

    app.chat_unread = unread_total

    @router.route("GET", "/api/channels", perm="chat.usar")
    def channels(req):
        general_channel_id()
        ids = channel_ids_for(req.user)
        if not ids:
            return {"items": []}
        unread = unread_by_channel(req.user["id"], ids)
        rows = db.query(f"SELECT c.*, p.key AS project_key, p.color AS project_color, "
                        f"(SELECT MAX(id) FROM messages m WHERE m.channel_id = c.id) AS last_id FROM channels c "
                        f"LEFT JOIN projects p ON p.id = c.project_id WHERE c.id IN ({','.join('?' * len(ids))}) "
                        f"ORDER BY CASE c.kind WHEN 'general' THEN 0 WHEN 'proyecto' THEN 1 ELSE 2 END, c.name", ids)
        for r in rows:
            r["unread"] = unread.get(r["id"], 0)
        return {"items": rows}

    @router.route("POST", "/api/channels", perm="chat.canales", idempotent=True)
    def new_channel(req):
        data = req.json
        name = clean_text(data.get("name"), "nombre del canal", 60, required=True)
        with db.tx() as c:
            pid = None
            if data.get("project_id"):
                pid = clean_int(data["project_id"], "proyecto")
            cur = c.execute("INSERT INTO channels(uid, name, kind, project_id, created_by, created_at) "
                            "VALUES (?,?,?,?,?,?)", (clean_uid(data.get("uid") or app.new_uid()), name,
                                                     "proyecto" if pid else "personalizado", pid, req.user["id"],
                                                     now_iso()))
            log_activity(db, c, req.user, "channel", cur.lastrowid, name, "created", f"Creó el canal #{name}")
            db.emit(c, "channel", "channel", cur.lastrowid, actor_id=req.user["id"])
            return Response.json({"id": cur.lastrowid}, 201)

    @router.route("PATCH", "/api/channels/{cid}", perm="chat.canales", idempotent=True)
    def edit_channel(req, cid):
        ch = get_channel(req.user, cid)
        if ch["kind"] == "general":
            raise bad_request("El canal general no se puede modificar.")
        data = req.json
        with db.tx() as c:
            if "name" in data:
                c.execute("UPDATE channels SET name=? WHERE id=?",
                          (clean_text(data["name"], "nombre", 60, required=True), ch["id"]))
            if "archived" in data:
                c.execute("UPDATE channels SET archived=? WHERE id=?", (1 if data["archived"] else 0, ch["id"]))
                log_activity(db, c, req.user, "channel", ch["id"], ch["name"], "archived",
                             f"{'Archivó' if data['archived'] else 'Recuperó'} el canal #{ch['name']}")
            db.emit(c, "channel", "channel", ch["id"], actor_id=req.user["id"])
        return {"ok": True}

    def enrich(rows: list[dict]) -> list[dict]:
        if not rows:
            return rows
        un = names(db.conn)
        ids = [r["id"] for r in rows]
        marks = ",".join("?" * len(ids))
        reads: dict[int, list] = {}
        for r in db.query(f"SELECT message_id, user_id, read_at FROM message_reads WHERE message_id IN ({marks})", ids):
            reads.setdefault(r["message_id"], []).append({"user_id": r["user_id"], "read_at": r["read_at"],
                                                          "user_name": un.get(r["user_id"])})
        atts = app.attachments.list_for_many("message", ids)
        reply_ids = list({r["reply_to_id"] for r in rows if r["reply_to_id"]})
        replies = {}
        if reply_ids:
            for r in db.query(f"SELECT id, user_id, body FROM messages WHERE id IN ({','.join('?' * len(reply_ids))})",
                              reply_ids):
                replies[r["id"]] = {"id": r["id"], "user_name": un.get(r["user_id"]), "body": r["body"][:240]}
        incidents = {}
        inc_ids = list({r["incident_id"] for r in rows if r["incident_id"]})
        if inc_ids:
            for r in db.query(f"SELECT id, key, title, status, priority FROM incidents WHERE id IN "
                              f"({','.join('?' * len(inc_ids))})", inc_ids):
                incidents[r["id"]] = r
        work = {}
        for lk in db.query(f"SELECT * FROM links WHERE from_type='message' AND from_id IN ({marks})", ids):
            s = summary(db, lk["to_type"], lk["to_id"])
            if s:
                work.setdefault(lk["from_id"], []).append(s)
        for r in rows:
            r["user_name"] = un.get(r["user_id"])
            r["read_by"] = reads.get(r["id"], [])
            r["attachments"] = atts.get(r["id"], [])
            r["reply_to"] = replies.get(r["reply_to_id"]) if r["reply_to_id"] else None
            r["incident"] = incidents.get(r["incident_id"]) if r["incident_id"] else None
            r["work"] = work.get(r["id"], [])
        return rows

    @router.route("GET", "/api/messages", perm="chat.usar")
    def list_messages(req):
        ch = get_channel(req.user, req.q("channel") or general_channel_id())
        cid = ch["id"]
        limit = min(req.q_int("limit", 50), 200)
        before, after, around = req.q_int("before"), req.q_int("after"), req.q_int("around")
        q = (req.q("q") or "").strip()
        if q:
            rows = db.query("SELECT * FROM messages WHERE channel_id=? AND body LIKE ? ORDER BY id DESC LIMIT ?",
                            (cid, f"%{q}%", limit))
            rows.reverse()
        elif around:
            older = db.query("SELECT * FROM messages WHERE channel_id=? AND id <= ? ORDER BY id DESC LIMIT ?",
                             (cid, around, limit // 2 + 1))
            newer = db.query("SELECT * FROM messages WHERE channel_id=? AND id > ? ORDER BY id ASC LIMIT ?",
                             (cid, around, limit // 2))
            rows = list(reversed(older)) + newer
        elif after is not None:
            rows = db.query("SELECT * FROM messages WHERE channel_id=? AND id > ? ORDER BY id ASC LIMIT ?",
                            (cid, after, limit))
        else:
            rows = db.query("SELECT * FROM messages WHERE channel_id=? AND id < ? ORDER BY id DESC LIMIT ?",
                            (cid, before, limit)) if before else \
                db.query("SELECT * FROM messages WHERE channel_id=? ORDER BY id DESC LIMIT ?", (cid, limit))
            rows.reverse()
        has_older = bool(rows) and bool(db.scalar("SELECT 1 FROM messages WHERE channel_id=? AND id < ? LIMIT 1",
                                                  (cid, rows[0]["id"])))
        return {"channel": ch, "items": enrich(rows), "has_older": has_older, "unread": unread_total(req.user)}

    @router.route("GET", "/api/messages/{mid}/locate", perm="chat.usar")
    def locate(req, mid):
        row = db.query_one("SELECT id, channel_id FROM messages WHERE id=?", (mid,))
        if not row:
            raise not_found("El mensaje no existe.")
        get_channel(req.user, row["channel_id"])
        return row

    def mark_read_upto(c, user_id: int, channel_id: int, last_id: int) -> None:
        prev = c.execute("SELECT last_read_id FROM chat_state WHERE user_id=? AND channel_id=?",
                         (user_id, channel_id)).fetchone()
        prev = prev[0] if prev else 0
        if last_id <= prev:
            return
        ts = now_iso()
        ids = [r[0] for r in c.execute("SELECT id FROM messages WHERE channel_id=? AND id > ? AND id <= ? AND "
                                       "user_id <> ?", (channel_id, prev, last_id, user_id))]
        c.executemany("INSERT OR IGNORE INTO message_reads(message_id, user_id, read_at) VALUES (?,?,?)",
                      [(i, user_id, ts) for i in ids])
        c.execute("INSERT INTO chat_state(user_id, channel_id, last_read_id) VALUES (?, ?, ?) ON CONFLICT(user_id, "
                  "channel_id) DO UPDATE SET last_read_id = MAX(last_read_id, excluded.last_read_id)",
                  (user_id, channel_id, last_id))
        if ids:
            marks = ",".join("?" * len(ids))
            c.execute(f"UPDATE notifications SET read_at=? WHERE user_id=? AND type IN ('message_new','mention') AND "
                      f"entity_type='message' AND read_at IS NULL AND entity_id IN ({marks})", [ts, user_id] + ids)
            db.emit(c, "chat_read", "message", last_id, actor_id=user_id)
            db.emit(c, "notification", None, None, user_id=user_id)

    @router.route("POST", "/api/messages", perm="chat.usar", idempotent=True)
    def send(req):
        data = req.json
        uid = clean_uid(data.get("uid"))
        body = clean_text(data.get("body"), "mensaje", 20000)
        if not body and not data.get("with_attachments") and not data.get("incident"):
            raise bad_request("El mensaje está vacío.", "mensaje_vacio")
        user = req.user
        ch = get_channel(user, data.get("channel_id") or general_channel_id())
        with db.tx() as c:
            dup = c.execute("SELECT id FROM messages WHERE uid=?", (uid,)).fetchone()
            if dup:
                return {"id": dup[0], "duplicate": True}
            reply_to = data.get("reply_to_id")
            if reply_to and not c.execute("SELECT 1 FROM messages WHERE id=? AND channel_id=?",
                                          (reply_to, ch["id"])).fetchone():
                raise bad_request("El mensaje al que respondes no existe en este canal.")
            incident_id = find_entity(db, user, "incident", str(data["incident"]))["id"] if data.get("incident") \
                else None
            cur = c.execute("INSERT INTO messages(uid, channel_id, user_id, body, reply_to_id, incident_id, created_at) "
                            "VALUES (?,?,?,?,?,?,?)", (uid, ch["id"], user["id"], body, reply_to or None, incident_id,
                                                       now_iso()))
            mid = cur.lastrowid
            mark_read_upto(c, user["id"], ch["id"], mid)
            preview = body[:160] if body else ("Ha compartido una incidencia" if incident_id else "Ha enviado archivos")
            label = f"#{ch['name']}" if ch["kind"] != "general" else "el chat"
            mentioned = mentioned_users(c, body)
            notify(db, c, mentioned, "mention", f"{user['display_name']} te ha mencionado en {label}", preview,
                   f"mention-msg-{mid}", "message", mid, exclude=user["id"], urgent=True)
            recipients = channel_audience(c, ch)
            notify(db, c, [u for u in recipients if u not in mentioned], "message_new",
                   f"{user['display_name']} en {label}", preview, f"msg-{mid}", "message", mid, exclude=user["id"])
            db.index(c, "message", mid, ch["project_id"], "", "", body)
            db.emit(c, "message", "message", mid, actor_id=user["id"])
            return Response.json({"id": mid}, 201)

    def channel_audience(c, ch) -> list[int]:
        users = [r[0] for r in c.execute("SELECT id FROM users WHERE active=1")]
        if ch["project_id"] is None:
            return users
        members = {r[0] for r in c.execute("SELECT user_id FROM project_members WHERE project_id=?",
                                           (ch["project_id"],))}
        seers = {r[0] for r in c.execute("SELECT u.id FROM users u JOIN role_permissions rp ON rp.role_id=u.role_id "
                                         "WHERE rp.perm='proyectos.ver_todos'")}
        return [u for u in users if u in members or u in seers]

    @router.route("PATCH", "/api/messages/{mid}", perm="chat.usar", idempotent=True)
    def edit(req, mid):
        body = clean_text(req.json.get("body"), "mensaje", 20000, required=True)
        user = req.user
        with db.tx() as c:
            row = c.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
            if not row:
                raise not_found("El mensaje no existe.")
            if row["user_id"] != user["id"]:
                raise HttpError(403, "sin_permiso", "Solo puedes editar tus propios mensajes.")
            if row["body"] == body:
                return {"unchanged": True}
            ts = now_iso()
            c.execute("INSERT INTO edits(entity_type, entity_id, previous_body, edited_at, edited_by) "
                      "VALUES ('message', ?, ?, ?, ?)", (row["id"], row["body"], ts, user["id"]))
            c.execute("UPDATE messages SET body=?, edited_at=? WHERE id=?", (body, ts, row["id"]))
            proj = c.execute("SELECT project_id FROM channels WHERE id=?", (row["channel_id"],)).fetchone()[0]
            db.index(c, "message", row["id"], proj, "", "", body)
            db.emit(c, "message", "message", row["id"], actor_id=user["id"])
            return {"ok": True, "edited_at": ts}

    @router.route("GET", "/api/messages/{mid}/edits", perm="chat.usar")
    def edits(req, mid):
        return {"items": db.query("SELECT previous_body, edited_at FROM edits WHERE entity_type='message' "
                                  "AND entity_id=? ORDER BY id", (mid,))}

    @router.route("POST", "/api/messages/read", perm="chat.usar")
    def mark_read(req):
        ch = get_channel(req.user, req.json.get("channel_id") or general_channel_id())
        last_id = clean_int(req.json.get("last_id") or 0, "último mensaje", 0)
        with db.tx() as c:
            mark_read_upto(c, req.user["id"], ch["id"], last_id)
        return {"unread": unread_total(req.user)}

    @router.route("POST", "/api/messages/{mid}/remind", perm="chat.usar", idempotent=True)
    def remind(req, mid):
        data = req.json
        at = data.get("remind_at")
        try:
            when = parse_iso(at)
        except (TypeError, ValueError):
            raise bad_request("Indica cuándo quieres el recordatorio.") from None
        if when < datetime.now(timezone.utc):
            raise bad_request("La hora del recordatorio ya ha pasado.")
        row = db.query_one("SELECT id, channel_id FROM messages WHERE id=?", (mid,))
        if not row:
            raise not_found("El mensaje no existe.")
        get_channel(req.user, row["channel_id"])
        with db.tx() as c:
            cur = c.execute("INSERT INTO reminders(user_id, message_id, remind_at, note, created_at) VALUES (?,?,?,?,?)",
                            (req.user["id"], row["id"], at, clean_text(data.get("note"), "nota", 300), now_iso()))
        return Response.json({"id": cur.lastrowid}, 201)

    def fire_reminders():
        due = db.query("SELECT r.*, m.body FROM reminders r JOIN messages m ON m.id = r.message_id "
                       "WHERE r.done=0 AND r.remind_at <= ?", (now_iso(),))
        if not due:
            return
        with db.tx() as c:
            for r in due:
                notify(db, c, [r["user_id"]], "reminder", "Recordatorio de un mensaje",
                       (r["note"] + " — " if r["note"] else "") + r["body"][:200], f"reminder-{r['id']}", "message",
                       r["message_id"], urgent=True)
                c.execute("UPDATE reminders SET done=1 WHERE id=?", (r["id"],))

    app.fire_reminders = fire_reminders
    _ = auth
