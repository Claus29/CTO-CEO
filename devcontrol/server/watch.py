"""Avisos a partir del registro de actividad: quién se entera de cada cambio.

Cada acción del programa ya se anota en el historial (`log_activity`). Aquí se decide, para cada una, a quién
avisar y con qué texto, sin tocar los avisos concretos que ya existían (asignaciones, verificaciones, menciones…):
quien ya ha recibido un aviso de ese elemento en la misma acción no recibe otro.

- Seguir: cualquiera puede seguir una tarea, incidencia, idea, página de la wiki, decisión o un proyecto entero.
  Quien crea, recibe o comenta algo lo sigue automáticamente.
- Sin spam: nunca se avisa a quien hace el cambio y las ediciones seguidas de la misma persona sobre el mismo
  elemento (2 minutos) se juntan en un solo aviso.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .common import NOTIFICATION_PRESETS, NOTIFICATION_TYPES, clean_choice, clean_int, users_with_perm
from .db import now_iso
from .entities import find_entity
from .web import bad_request

COALESCE_SECONDS = 120
FOLLOWABLE = {"task", "incident", "idea", "wiki", "decision", "project", "user"}   # user: avisos de conexión
TASK_SCOPES = {"proyecto": "Todas las tareas de mis proyectos", "mio": "Solo las mías y las que sigo"}
CHANNEL_MODES = {"todo": "Todos los mensajes", "menciones": "Solo menciones y respuestas",
                 "silencio": "Silenciado (solo si te mencionan)"}
STATUS_ACTIONS = {"started", "completed", "reopened", "status", "blocked", "unblocked", "verification_requested",
                  "verified", "rejected", "resolved", "duplicate"}
# Acciones que ya avisan por su cuenta (o que no interesan): solo se usan para seguir automáticamente.
SILENT_ACTIONS = {"comment", "seen", "github"}
TITLE_SQL = {"task": "SELECT key, title, project_id, created_by, assignee_id FROM tasks WHERE id=?",
             "incident": "SELECT key, title, project_id, created_by, assignee_id FROM incidents WHERE id=?",
             "idea": "SELECT NULL AS key, title, project_id, created_by, NULL AS assignee_id FROM ideas WHERE id=?",
             "wiki": "SELECT NULL AS key, title, project_id, created_by, NULL AS assignee_id FROM wiki_pages WHERE id=?",
             "decision": "SELECT key, title, project_id, created_by, NULL AS assignee_id FROM decisions WHERE id=?",
             "version": "SELECT NULL AS key, name AS title, project_id, created_by, NULL AS assignee_id, status "
                        "FROM versions WHERE id=?",
             "build": "SELECT NULL AS key, number || ' · ' || platform AS title, project_id, created_by, "
                      "NULL AS assignee_id FROM builds WHERE id=?",
             "test_run": "SELECT NULL AS key, title, project_id, created_by, NULL AS assignee_id FROM test_runs "
                         "WHERE id=?",
             "date": "SELECT NULL AS key, title || ' (' || date || ')' AS title, project_id, created_by, "
                     "NULL AS assignee_id FROM project_dates WHERE id=?",
             "goal": "SELECT NULL AS key, title, NULL AS project_id, created_by, NULL AS assignee_id FROM goals "
                     "WHERE id=?"}


def _ago(seconds: float) -> str:
    t = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def sentence(actor: str, summary: str) -> str:
    """«Cambió la prioridad de T-003» → «Luis cambió la prioridad de T-003»."""
    summary = (summary or "").strip()
    return f"{actor} {summary[:1].lower()}{summary[1:]}" if summary else actor


# -- seguir ------------------------------------------------------------------------------------------
def follow(c, user_id, entity_type: str, entity_id) -> None:
    if user_id and entity_id:
        c.execute("INSERT OR IGNORE INTO follows(user_id, entity_type, entity_id, created_at) VALUES (?,?,?,?)",
                  (user_id, entity_type, entity_id, now_iso()))


def followers(c, entity_type: str, entity_id) -> set[int]:
    return {r[0] for r in c.execute("SELECT f.user_id FROM follows f JOIN users u ON u.id=f.user_id "
                                    "WHERE u.active=1 AND f.entity_type=? AND f.entity_id=?", (entity_type, entity_id))}


def settings_for(c, user_id: int) -> dict:
    row = c.execute("SELECT quiet_from, quiet_to, digest_time, task_scope FROM notify_settings WHERE user_id=?",
                    (user_id,)).fetchone()
    base = {"quiet_from": None, "quiet_to": None, "digest_time": "09:00", "task_scope": "proyecto"}
    if row:
        base.update({k: row[k] for k in base if row[k] is not None})
    return base


def scoped_out(c, user_ids) -> set[int]:
    """Personas que solo quieren saber de lo suyo (no de todas las tareas del proyecto)."""
    ids = list(set(user_ids))
    if not ids:
        return set()
    marks = ",".join("?" * len(ids))
    return {r[0] for r in c.execute(f"SELECT user_id FROM notify_settings WHERE task_scope='mio' AND user_id IN "
                                    f"({marks})", ids)}


# -- enviar -----------------------------------------------------------------------------------------
def push(db, c, user_ids, ntype: str, title: str, line: str, entity_type: str | None, entity_id,
         entity_key: str | None = None, actor_id: int | None = None, coalesce: bool = False, urgent: bool = False,
         dedupe: str | None = None) -> int:
    """Crea (o amplía) un aviso por persona. Devuelve cuántas personas lo reciben."""
    targets = {u for u in user_ids if u and u != actor_id}
    if not targets:
        return 0
    # Quien ya tiene un aviso concreto de esta misma acción (asignado, verificar…) no recibe otro.
    already = {u for u, et, ei in getattr(db, "tx_notified", ()) if et == entity_type and ei == entity_id}
    marks = ",".join("?" * len(targets))
    active = {r[0] for r in c.execute(f"SELECT id FROM users WHERE active=1 AND id IN ({marks})", list(targets))}
    off = {r[0] for r in c.execute(f"SELECT user_id FROM notification_prefs WHERE type=? AND in_app=0 AND user_id IN "
                                   f"({marks})", [ntype] + list(targets))}
    ts = now_iso()
    sent = 0
    for uid in sorted(active - already - off):
        if coalesce and entity_type and actor_id:
            prev = c.execute(
                "SELECT id, body, type FROM notifications WHERE user_id=? AND entity_type=? AND entity_id=? AND "
                "dedupe_key LIKE ? AND read_at IS NULL AND created_at >= ? ORDER BY id DESC LIMIT 1",
                (uid, entity_type, entity_id, f"chg-{entity_type}-{entity_id}-{actor_id}-%",
                 _ago(COALESCE_SECONDS))).fetchone()
            if prev:
                lines = [x for x in prev["body"].split("\n") if x]
                if line not in lines:
                    lines.append(line)
                body = "\n".join(lines[-6:])
                new_type = "status_changed" if ntype == "status_changed" else prev["type"]
                c.execute("UPDATE notifications SET body=?, type=? WHERE id=?", (body[:500], new_type, prev["id"]))
                db.emit(c, "notification", "notification", prev["id"], user_id=uid)
                sent += 1
                continue
        key = dedupe or (f"chg-{entity_type}-{entity_id}-{actor_id}-{ts}" if coalesce else
                         f"{ntype}-{entity_type}-{entity_id}-{ts}")
        cur = c.execute(
            "INSERT OR IGNORE INTO notifications(user_id, type, title, body, entity_type, entity_id, entity_key, "
            "urgent, dedupe_key, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (uid, ntype, title[:200], line[:500], entity_type, entity_id, entity_key, 1 if urgent else 0, key, ts))
        if cur.rowcount:
            sent += 1
            db.emit(c, "notification", "notification", cur.lastrowid, user_id=uid)
    return sent


# -- de la actividad a los avisos -------------------------------------------------------------------------
class Watcher:
    def __init__(self, app):
        self.app = app
        self.db = app.db

    def __call__(self, c, user, etype, eid, ekey, action, summary, details, project_id):
        if not user or not eid:
            return
        handler = getattr(self, f"on_{etype}", None)
        if handler:
            handler(c, user, etype, eid, ekey, action, summary, project_id)

    def _row(self, c, etype, eid):
        sql = TITLE_SQL.get(etype)
        row = c.execute(sql, (eid,)).fetchone() if sql else None
        return dict(row) if row else None

    def _project_followers(self, c, project_id) -> set[int]:
        return followers(c, "project", project_id) if project_id else set()

    # tareas e incidencias ------------------------------------------------------------------------------
    def _work_item(self, c, user, etype, eid, ekey, action, summary, project_id, created_type, updated_type):
        row = self._row(c, etype, eid)
        if not row:
            return
        name = user["display_name"]
        key = row["key"] or ekey
        if action == "comment":
            follow(c, user["id"], etype, eid)
            return
        if action in SILENT_ACTIONS:
            return
        if action == "created":
            follow(c, row["created_by"], etype, eid)
            follow(c, row["assignee_id"], etype, eid)
            audience = set(self._project_followers(c, row["project_id"]))
            if created_type:
                perm = "tareas.ver" if etype == "task" else "incidencias.ver"
                members = set(users_with_perm(c, perm, row["project_id"]))
                audience |= members - scoped_out(c, members)
            audience.discard(row["assignee_id"])   # ya recibe «Te han asignado…»
            push(self.db, c, audience, created_type or "incident_created", f"Nueva {'tarea' if etype == 'task' else 'incidencia'} {key}",
                 f"{name}: {row['title']}", etype, eid, key, actor_id=user["id"])
            return
        if action == "assigned":
            follow(c, user["id"], etype, eid)
        recipients = {row["created_by"], row["assignee_id"]} | followers(c, etype, eid) | \
            self._project_followers(c, row["project_id"])
        ntype = "status_changed" if action in STATUS_ACTIONS else updated_type
        push(self.db, c, recipients, ntype, f"{key} · {row['title']}", sentence(name, summary), etype, eid, key,
             actor_id=user["id"], coalesce=True)

    def on_task(self, c, user, etype, eid, ekey, action, summary, project_id):
        self._work_item(c, user, etype, eid, ekey, action, summary, project_id, "task_created", "task_updated")

    def on_incident(self, c, user, etype, eid, ekey, action, summary, project_id):
        # Al crear una incidencia ya avisa su propio módulo; aquí solo se añade a quien sigue el proyecto.
        self._work_item(c, user, etype, eid, ekey, action, summary, project_id, None, "incident_updated")

    # planificación --------------------------------------------------------------------------------------
    def on_idea(self, c, user, etype, eid, ekey, action, summary, project_id):
        row = self._row(c, "idea", eid)
        if not row:
            return
        name = user["display_name"]
        if action == "created":
            follow(c, row["created_by"], "idea", eid)
            push(self.db, c, set(users_with_perm(c, "planificacion.ver", row["project_id"])) |
                 self._project_followers(c, row["project_id"]), "idea", "Idea nueva", f"{name}: {row['title']}",
                 "idea", eid, actor_id=user["id"])
            return
        voters = {r[0] for r in c.execute("SELECT user_id FROM idea_votes WHERE idea_id=?", (eid,))}
        push(self.db, c, {row["created_by"]} | voters | followers(c, "idea", eid), "idea", f"Idea · {row['title']}",
             sentence(name, summary), "idea", eid, actor_id=user["id"], coalesce=True)

    def on_version(self, c, user, etype, eid, ekey, action, summary, project_id):
        row = self._row(c, "version", eid)
        if not row:
            return
        name = user["display_name"]
        if action == "created":
            push(self.db, c, users_with_perm(c, "planificacion.ver", row["project_id"]), "release",
                 f"Nueva versión planificada: {row['title']}", f"{name} la ha creado", "version", eid,
                 actor_id=user["id"])
        elif row.get("status") == "publicada":
            push(self.db, c, users_with_perm(c, "tareas.ver", row["project_id"]), "release",
                 f"Versión {row['title']} publicada", sentence(name, summary), "version", eid, actor_id=user["id"],
                 dedupe=f"version-published-{eid}")

    def on_goal(self, c, user, etype, eid, ekey, action, summary, project_id):
        row = self._row(c, "goal", eid)
        if row and action == "done":
            push(self.db, c, users_with_perm(c, "planificacion.ver"), "release", "Objetivo de la semana cumplido",
                 f"{user['display_name']}: {row['title']}", "goal", eid, actor_id=user["id"],
                 dedupe=f"goal-done-{eid}")

    def on_date(self, c, user, etype, eid, ekey, action, summary, project_id):
        row = self._row(c, "date", eid)
        if row and action == "created":
            push(self.db, c, users_with_perm(c, "planificacion.ver", row["project_id"]), "calendar",
                 "Nueva fecha en el calendario", f"{user['display_name']}: {row['title']}", "date", eid,
                 actor_id=user["id"])

    # calidad ---------------------------------------------------------------------------------------------
    def on_build(self, c, user, etype, eid, ekey, action, summary, project_id):
        row = self._row(c, "build", eid)
        if row and action == "created":
            push(self.db, c, users_with_perm(c, "calidad.ver", row["project_id"]), "quality",
                 f"Build nueva lista para probar: {row['title']}", sentence(user["display_name"], summary), "build",
                 eid, actor_id=user["id"])

    def on_test_run(self, c, user, etype, eid, ekey, action, summary, project_id):
        row = self._row(c, "test_run", eid)
        if row and action == "created":
            push(self.db, c, users_with_perm(c, "calidad.ver", row["project_id"]), "quality",
                 f"Nueva ronda de pruebas: {row['title']}", sentence(user["display_name"], summary), "test_run", eid,
                 actor_id=user["id"])

    # conocimiento ----------------------------------------------------------------------------------------
    def on_wiki(self, c, user, etype, eid, ekey, action, summary, project_id):
        row = self._row(c, "wiki", eid)
        if not row:
            return
        name = user["display_name"]
        if action == "created":
            follow(c, row["created_by"], "wiki", eid)
            push(self.db, c, users_with_perm(c, "conocimiento.ver", row["project_id"]), "wiki",
                 f"Página nueva en la wiki: {row['title']}", f"{name} la ha creado", "wiki", eid, actor_id=user["id"])
        else:
            follow(c, user["id"], "wiki", eid)
            push(self.db, c, {row["created_by"]} | followers(c, "wiki", eid), "wiki", f"Wiki · {row['title']}",
                 sentence(name, summary), "wiki", eid, actor_id=user["id"], coalesce=True)

    def on_decision(self, c, user, etype, eid, ekey, action, summary, project_id):
        row = self._row(c, "decision", eid)
        if not row:
            return
        name = user["display_name"]
        if action == "created":
            follow(c, row["created_by"], "decision", eid)
            push(self.db, c, users_with_perm(c, "conocimiento.ver", row["project_id"]), "wiki",
                 f"Decisión propuesta {row['key']}", f"{name}: {row['title']}", "decision", eid, row["key"],
                 actor_id=user["id"])
            return
        status = c.execute("SELECT status FROM decisions WHERE id=?", (eid,)).fetchone()[0]
        if status in ("aceptada", "rechazada"):
            push(self.db, c, users_with_perm(c, "conocimiento.ver", row["project_id"]), "wiki",
                 f"Decisión {row['key']} {status}", row["title"], "decision", eid, row["key"], actor_id=user["id"],
                 dedupe=f"decision-{eid}-{status}")
        push(self.db, c, {row["created_by"]} | followers(c, "decision", eid), "wiki", f"{row['key']} · {row['title']}",
             sentence(name, summary), "decision", eid, row["key"], actor_id=user["id"], coalesce=True)

    # equipo ----------------------------------------------------------------------------------------------
    def on_absence(self, c, user, etype, eid, ekey, action, summary, project_id):
        if action != "created":
            return
        row = c.execute("SELECT a.*, u.display_name FROM absences a JOIN users u ON u.id=a.user_id WHERE a.id=?",
                        (eid,)).fetchone()
        if not row:
            return
        mates = {r[0] for r in c.execute(
            "SELECT DISTINCT pm2.user_id FROM project_members pm1 JOIN project_members pm2 "
            "ON pm2.project_id=pm1.project_id WHERE pm1.user_id=?", (row["user_id"],))}
        mates |= set(users_with_perm(c, "equipo.ver"))
        mates.discard(row["user_id"])
        span = row["start_date"] if row["start_date"] == row["end_date"] else f"{row['start_date']} → {row['end_date']}"
        push(self.db, c, mates, "team_absence", f"{row['display_name']} estará ausente",
             span + (f" · {row['note']}" if row["note"] else ""), "absence", eid, actor_id=user["id"])

    def on_user(self, c, user, etype, eid, ekey, action, summary, project_id):
        if action in ("password_reset", "2fa_reset", "sessions_closed") and eid != user["id"]:
            texts = {"password_reset": "Un administrador ha cambiado tu contraseña.",
                     "2fa_reset": "Un administrador ha desactivado tu verificación en dos pasos.",
                     "sessions_closed": "Se han cerrado tus sesiones en todos los equipos."}
            push(self.db, c, [eid], "account", "Cambio en tu cuenta", texts[action], "user", eid,
                 actor_id=user["id"])
        elif action == "2fa_off":
            push(self.db, c, users_with_perm(c, "sistema.usuarios"), "system",
                 f"{user['display_name']} ha desactivado la verificación en dos pasos",
                 "Su cuenta queda protegida solo con la contraseña.", "user", eid, actor_id=user["id"])


# -- rutas: seguir, ajustes y modo de cada canal ----------------------------------------------------------------
def register(router, app):
    db = app.db
    watcher = Watcher(app)
    db.activity_hooks = [*getattr(db, "activity_hooks", []), watcher]
    app.watcher = watcher

    def check_visible(user, etype: str, eid: int) -> None:
        if etype == "user":
            if eid == user["id"] or not db.query_one("SELECT 1 FROM users WHERE id=? AND active=1", (eid,)):
                raise bad_request("Elige a otra persona del equipo.")
            return
        if etype == "project":
            from .common import check_project_access
            check_project_access(db, user, eid)
        else:
            find_entity(db, user, etype, eid)

    @router.route("GET", "/api/follows")
    def get_follow(req):
        etype = clean_choice(req.q("entity_type"), "tipo", {k: k for k in FOLLOWABLE})
        eid = clean_int(req.q("entity_id"), "elemento")
        check_visible(req.user, etype, eid)
        with db.tx() as c:
            people = followers(c, etype, eid)
        return {"following": req.user["id"] in people, "count": len(people)}

    @router.route("POST", "/api/follows", idempotent=True)
    def set_follow(req):
        data = req.json
        etype = clean_choice(data.get("entity_type"), "tipo", {k: k for k in FOLLOWABLE})
        eid = clean_int(data.get("entity_id"), "elemento")
        check_visible(req.user, etype, eid)
        with db.tx() as c:
            if data.get("follow", True):
                follow(c, req.user["id"], etype, eid)
            else:
                c.execute("DELETE FROM follows WHERE user_id=? AND entity_type=? AND entity_id=?",
                          (req.user["id"], etype, eid))
            people = followers(c, etype, eid)
        return {"following": req.user["id"] in people, "count": len(people)}

    @router.route("GET", "/api/follows/users")
    def followed_users(req):
        """Personas de las que quiero saber cuándo se conectan (también salen arriba, junto a «Conectado»)."""
        rows = db.query("SELECT f.entity_id FROM follows f JOIN users u ON u.id=f.entity_id WHERE f.user_id=? "
                        "AND f.entity_type='user' AND u.active=1 ORDER BY f.created_at", (req.user["id"],))
        return {"user_ids": [r["entity_id"] for r in rows]}

    @router.route("GET", "/api/channels/{cid}/notify")
    def get_channel_mode(req, cid):
        row = db.query_one("SELECT mode FROM channel_prefs WHERE user_id=? AND channel_id=?", (req.user["id"], cid))
        return {"mode": row["mode"] if row else "todo", "modes": CHANNEL_MODES}

    @router.route("PUT", "/api/channels/{cid}/notify", idempotent=True)
    def set_channel_mode(req, cid):
        mode = clean_choice(req.json.get("mode"), "avisos del canal", CHANNEL_MODES)
        if not db.query_one("SELECT 1 FROM channels WHERE id=?", (int(cid),)):
            raise bad_request("El canal no existe.")
        with db.tx() as c:
            c.execute("INSERT INTO channel_prefs(user_id, channel_id, mode) VALUES (?,?,?) ON CONFLICT(user_id, "
                      "channel_id) DO UPDATE SET mode=excluded.mode", (req.user["id"], int(cid), mode))
            db.emit(c, "channel", "channel", int(cid), user_id=req.user["id"])
        return {"mode": mode}

    def apply_preset(c, uid: int, preset: str) -> None:
        keep = NOTIFICATION_PRESETS.get(preset)
        for t in NOTIFICATION_TYPES:
            on = 1 if keep is None or t in keep else 0
            c.execute("INSERT INTO notification_prefs(user_id, type, in_app, desktop) VALUES (?,?,1,?) "
                      "ON CONFLICT(user_id, type) DO UPDATE SET desktop=excluded.desktop", (uid, t, on))
        if preset == "solo_mio":
            save_settings(c, uid, {"task_scope": "mio"})
        elif preset == "todo":
            save_settings(c, uid, {"task_scope": "proyecto"})

    def save_settings(c, uid: int, data: dict) -> None:
        cur = settings_for(c, uid)
        for k in ("quiet_from", "quiet_to", "digest_time"):
            if k in data:
                v = data[k] or None
                if v is not None and not (isinstance(v, str) and len(v) == 5 and v[2] == ":" and
                                          v[:2].isdigit() and v[3:].isdigit() and int(v[:2]) < 24 and int(v[3:]) < 60):
                    raise bad_request("Hora no válida (usa HH:MM).")
                cur[k] = v if v is not None or k != "digest_time" else "09:00"
        if "task_scope" in data:
            cur["task_scope"] = clean_choice(data["task_scope"], "tareas de las que avisar", TASK_SCOPES)
        c.execute("INSERT INTO notify_settings(user_id, quiet_from, quiet_to, digest_time, task_scope, updated_at) "
                  "VALUES (?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET quiet_from=excluded.quiet_from, "
                  "quiet_to=excluded.quiet_to, digest_time=excluded.digest_time, task_scope=excluded.task_scope, "
                  "updated_at=excluded.updated_at",
                  (uid, cur["quiet_from"], cur["quiet_to"], cur["digest_time"], cur["task_scope"], now_iso()))

    app.notify_settings_save = save_settings
    app.notify_preset = apply_preset


# -- avisos de conexión y desconexión ---------------------------------------------------------------------
PRESENCE_GRACE = 90        # al arrancar el servidor todos se reconectan: eso no se avisa
OFFLINE_CONFIRM = 30       # una desconexión solo se avisa si dura (los cortes breves de red no)


class PresenceNotifier:
    """Recibe cada pocos segundos quién está conectado y avisa a quien sigue a esa persona."""

    def __init__(self, db, clock=None):
        import time
        self.db = db
        self.clock = clock or time.monotonic
        self.started = self.clock()
        self.prev: set[int] = set()
        self.ever_online: set[int] = set()
        self.offline_since: dict[int, float] = {}
        self.announced_offline: set[int] = set()

    def _announce(self, uid: int, online: bool) -> None:
        with self.db.tx() as c:
            people = followers(c, "user", uid) - {uid}
            if not people:
                return
            row = c.execute("SELECT display_name FROM users WHERE id=?", (uid,)).fetchone()
            name = row[0] if row else "Alguien"
            from datetime import datetime
            hora = datetime.now().strftime("%H:%M")
            push(self.db, c, people, "presence", f"{name} se ha {'conectado' if online else 'desconectado'}",
                 f"{'En línea' if online else 'Desconectado'} desde las {hora}.", "presence", uid,
                 dedupe=f"presence-{uid}-{'on' if online else 'off'}-{now_iso()}")

    def update(self, online: set[int]) -> None:
        now = self.clock()
        in_grace = now - self.started < PRESENCE_GRACE
        for uid in online - self.prev:
            if uid in self.offline_since:              # volvió antes de darlo por desconectado: nada
                del self.offline_since[uid]
            elif uid in self.announced_offline or (uid not in self.ever_online and not in_grace):
                self._announce(uid, True)
            self.announced_offline.discard(uid)
            self.ever_online.add(uid)
        for uid in self.prev - online:
            self.offline_since[uid] = now
        for uid, since in list(self.offline_since.items()):
            if now - since >= OFFLINE_CONFIRM:
                del self.offline_since[uid]
                self.announced_offline.add(uid)
                self._announce(uid, False)
        self.prev = set(online)
