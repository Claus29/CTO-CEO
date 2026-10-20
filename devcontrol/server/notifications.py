"""Notificaciones, preferencias por usuario y avisos programados (sin duplicados)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .common import NOTIFICATION_TYPES, OPEN_INCIDENT, all_users, clean_int, notify, users_with_perm
from .db import parse_iso
from .incidents import sla_info
from .web import bad_request


def utc_ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def register(router, app):
    db = app.db

    @router.route("GET", "/api/notifications")
    def list_notifications(req):
        uid = req.user["id"]
        limit = min(req.q_int("limit", 50), 200)
        after = req.q_int("after")
        where, params = "user_id=?", [uid]
        if after is not None:
            where += " AND id > ?"
            params.append(after)
        if req.q("unread") == "1":
            where += " AND read_at IS NULL"
        rows = db.query(f"SELECT * FROM notifications WHERE {where} ORDER BY id DESC LIMIT ?", params + [limit])
        unread = db.scalar("SELECT COUNT(*) FROM notifications WHERE user_id=? AND read_at IS NULL", (uid,))
        return {"items": rows, "unread": unread}

    @router.route("POST", "/api/notifications/read")
    def mark_read(req):
        data = req.json
        uid = req.user["id"]
        from .db import now_iso
        with db.tx() as c:
            if data.get("all"):
                c.execute("UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL", (now_iso(), uid))
            else:
                ids = [clean_int(i, "id") for i in data.get("ids") or []]
                if ids:
                    c.execute(f"UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL AND id IN "
                              f"({','.join('?' * len(ids))})", [now_iso(), uid] + ids)
            db.emit(c, "notification", None, None, user_id=uid)
        return {"unread": db.scalar("SELECT COUNT(*) FROM notifications WHERE user_id=? AND read_at IS NULL", (uid,))}

    def prefs_for(uid: int) -> dict:
        stored = {r["type"]: r for r in db.query("SELECT * FROM notification_prefs WHERE user_id=?", (uid,))}
        items = [{"type": t, "label": label, "in_app": bool(stored[t]["in_app"]) if t in stored else True,
                  "desktop": bool(stored[t]["desktop"]) if t in stored else True} for t, label in NOTIFICATION_TYPES.items()]
        return {"items": items, "mute_until": db.scalar("SELECT mute_until FROM users WHERE id=?", (uid,))}

    app.notification_prefs = prefs_for

    @router.route("GET", "/api/notifications/prefs")
    def get_prefs(req):
        return prefs_for(req.user["id"])

    @router.route("PUT", "/api/notifications/prefs")
    def put_prefs(req):
        data = req.json
        uid = req.user["id"]
        with db.tx() as c:
            for item in data.get("items") or []:
                t = item.get("type")
                if t not in NOTIFICATION_TYPES:
                    raise bad_request(f"Tipo de aviso desconocido: {t}")
                c.execute("INSERT INTO notification_prefs(user_id, type, in_app, desktop) VALUES (?,?,?,?) "
                          "ON CONFLICT(user_id, type) DO UPDATE SET in_app=excluded.in_app, desktop=excluded.desktop",
                          (uid, t, 1 if item.get("in_app", True) else 0, 1 if item.get("desktop", True) else 0))
            if "mute_until" in data:
                mute = data["mute_until"]
                if mute is not None and not isinstance(mute, str):
                    raise bad_request("mute_until no válido.")
                c.execute("UPDATE users SET mute_until=? WHERE id=?", (mute, uid))
        return prefs_for(uid)


class NotificationJobs:
    """Avisos que no dependen de una acción concreta. Cada uno lleva su clave de deduplicación."""

    def __init__(self, app):
        self.app = app
        self.db = app.db

    def run(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        for job in (self.due_dates, self.stale, self.sla, self.renewals, self.standup, self.daily_summary,
                    self.weekly_summary):
            try:
                job(now)
            except Exception:  # un aviso que falla no debe parar a los demás
                self.app.log.exception("Error en el aviso programado %s", job.__name__)
        if hasattr(self.app, "fire_reminders"):
            self.app.fire_reminders()

    def due_dates(self, now: datetime) -> None:
        days = int(self.db.get_setting("due_soon_days"))
        today = now.date()
        limit = (today + timedelta(days=days)).isoformat()
        with self.db.tx() as c:
            for r in c.execute(f"SELECT id, key, title, target_date, assignee_id, created_by, project_id FROM incidents WHERE "
                               f"status IN ({','.join('?' * len(OPEN_INCIDENT))}) AND target_date IS NOT NULL AND "
                               f"target_date <= ?", (*OPEN_INCIDENT, limit)).fetchall():
                overdue = r["target_date"] < today.isoformat()
                kind = "overdue" if overdue else "soon"
                notify(self.db, c, [r["assignee_id"], r["created_by"]], "due_soon",
                       f"{r['key']} {'vencida' if overdue else 'vence pronto'} ({r['target_date']})", r["title"],
                       f"due-inc-{r['id']}-{r['target_date']}-{kind}", "incident", r["id"], r["key"], urgent=overdue)
            for r in c.execute("SELECT id, key, title, target_date, assignee_id, created_by FROM tasks WHERE "
                               "status<>'resuelto' AND target_date IS NOT NULL AND target_date <= ?", (limit,)).fetchall():
                overdue = r["target_date"] < today.isoformat()
                kind = "overdue" if overdue else "soon"
                notify(self.db, c, [r["assignee_id"], r["created_by"]], "due_soon",
                       f"Tarea {r['key']} {'vencida' if overdue else 'vence pronto'} ({r['target_date']})", r["title"],
                       f"due-task-{r['id']}-{r['target_date']}-{kind}", "task", r["id"], r["key"])

    def stale(self, now: datetime) -> None:
        days = int(self.db.get_setting("stale_days"))
        if days <= 0:
            return
        with self.db.tx() as c:
            for r in c.execute("SELECT id, key, title, updated_at, assignee_id, created_by FROM incidents WHERE status IN "
                               "('pendiente','en_progreso','bloqueada') AND updated_at < ?", (utc_ago(days),)).fetchall():
                notify(self.db, c, [r["assignee_id"], r["created_by"]], "incident_stale",
                       f"{r['key']} lleva {days}+ días sin cambios", r["title"],
                       f"stale-{r['id']}-{r['updated_at'][:10]}", "incident", r["id"], r["key"])

    def sla(self, now: datetime) -> None:
        if not self.db.get_setting("sla_enabled"):
            return
        rules = self.db.get_setting("sla") or {}
        with self.db.tx() as c:
            admins = users_with_perm(c, "sistema.usuarios")
            for r in c.execute(f"SELECT * FROM incidents WHERE status IN ({','.join('?' * len(OPEN_INCIDENT))})",
                               OPEN_INCIDENT).fetchall():
                r = dict(r)
                info = sla_info(r, rules, True)
                if not info:
                    continue
                cyc = r["cycle_started_at"][:19]
                target = [r["assignee_id"]] if r["assignee_id"] else users_with_perm(c, "incidencias.trabajar",
                                                                                      r["project_id"])
                if info.get("response_breached"):
                    notify(self.db, c, target, "sla", f"{r['key']}: sin primera respuesta en el plazo",
                           r["title"], f"sla-resp-{r['id']}", "incident", r["id"], r["key"], urgent=True)
                    notify(self.db, c, admins, "sla", f"Escalado: {r['key']} sin respuesta en el plazo", r["title"],
                           f"sla-resp-esc-{r['id']}", "incident", r["id"], r["key"], urgent=True)
                if info.get("resolve_breached"):
                    notify(self.db, c, target, "sla", f"{r['key']}: plazo de resolución vencido", r["title"],
                           f"sla-res-{r['id']}-{cyc}", "incident", r["id"], r["key"], urgent=True)
                    notify(self.db, c, admins, "sla", f"Escalado: {r['key']} supera el plazo de resolución", r["title"],
                           f"sla-res-esc-{r['id']}-{cyc}", "incident", r["id"], r["key"], urgent=True)
                elif info.get("resolve_ratio", 0) >= 0.75:
                    notify(self.db, c, target, "sla", f"{r['key']}: el 75 % del plazo ya ha pasado",
                           f"Vence el {info['resolve_due'][:16].replace('T', ' ')} (UTC) · {r['title']}",
                           f"sla-risk-{r['id']}-{cyc}", "incident", r["id"], r["key"])

    def renewals(self, now: datetime) -> None:
        today = now.date()
        with self.db.tx() as c:
            admins = users_with_perm(c, "planificacion.editar")
            for r in c.execute("SELECT * FROM renewals").fetchall():
                left = (date.fromisoformat(r["expires_on"]) - today).days
                for threshold in (30, 7, 1, 0):
                    if left <= threshold:
                        label = "caducada" if left < 0 else ("caduca hoy" if left == 0 else f"caduca en {left} días")
                        notify(self.db, c, [r["owner_id"]] if r["owner_id"] else admins, "renewal",
                               f"Renovación: {r['title']} {label}", f"Fecha: {r['expires_on']}",
                               f"renewal-{r['id']}-{r['expires_on']}-{threshold}", "renewal", r["id"],
                               urgent=threshold <= 1)
                        break

    def standup(self, now: datetime) -> None:
        if not self.db.get_setting("standup_enabled"):
            return
        if now.weekday() not in (self.db.get_setting("standup_weekdays") or []):
            return
        hour = int(self.db.get_setting("standup_hour"))
        day = now.date().isoformat()
        if now.hour < hour:
            return
        with self.db.tx() as c:
            filled = {r[0] for r in c.execute("SELECT user_id FROM standups WHERE date=?", (day,))}
            missing = [u for u in all_users(c) if u not in filled]
            notify(self.db, c, missing, "standup", "Rellena tu daily de hoy",
                   "Tres líneas: qué hiciste, qué harás hoy y si algo te bloquea.", f"standup-remind-{day}", "standup",
                   None)
            if now.hour >= hour + 2:
                names_ = {r[0]: r[1] for r in c.execute("SELECT id, display_name FROM users")}
                blockers = c.execute("SELECT user_id, blockers FROM standups WHERE date=? AND blockers<>''", (day,)).fetchall()
                body = (f"{len(filled)} de {len(filled) + len(missing)} lo han rellenado"
                        + (f" · Falta: {', '.join(names_.get(u, '?') for u in missing)}" if missing else "")
                        + (f" · Bloqueos: {', '.join(names_.get(b[0], '?') for b in blockers)}" if blockers else ""))
                notify(self.db, c, users_with_perm(c, "equipo.ver"), "standup", f"Resumen del daily · {day}", body,
                       f"standup-summary-{day}", "standup", None)

    def summary_text(self, since_iso: str, label: str) -> tuple[str, bool]:
        db = self.db
        created = db.scalar("SELECT COUNT(*) FROM incidents WHERE created_at >= ?", (since_iso,))
        resolved = db.scalar("SELECT COUNT(*) FROM resolutions WHERE entity_type='incident' AND state='verificada' AND "
                             "verified_at >= ?", (since_iso,))
        to_verify = db.scalar("SELECT COUNT(*) FROM incidents WHERE status='en_verificacion'")
        blocked = db.scalar("SELECT COUNT(*) FROM incidents WHERE status='bloqueada'")
        open_ = db.scalar(f"SELECT COUNT(*) FROM incidents WHERE status IN ({','.join('?' * len(OPEN_INCIDENT))})",
                          OPEN_INCIDENT)
        critical = db.scalar("SELECT COUNT(*) FROM incidents WHERE status IN ('pendiente','en_progreso','bloqueada') AND "
                             "priority='critica'")
        overdue = db.scalar("SELECT COUNT(*) FROM incidents WHERE status IN ('pendiente','en_progreso','bloqueada') AND "
                            "target_date < ?", (date.today().isoformat(),))
        tasks_done = db.scalar("SELECT COUNT(*) FROM tasks WHERE completed_at >= ?", (since_iso,))
        parts = [f"{label}: {created} nuevas, {resolved} resueltas", f"{open_} abiertas ({critical} críticas)",
                 f"{to_verify} pendientes de verificar", f"{blocked} bloqueadas", f"{overdue} vencidas",
                 f"{tasks_done} tareas completadas"]
        return " · ".join(parts), any([created, resolved, blocked, overdue, tasks_done, to_verify])

    def daily_summary(self, now: datetime) -> None:
        if now.hour < int(self.db.get_setting("daily_summary_hour")):
            return
        text, meaningful = self.summary_text(utc_ago(1), "Últimas 24 h")
        if not meaningful:
            return
        with self.db.tx() as c:
            notify(self.db, c, all_users(c), "daily_summary", f"Resumen diario · {now.strftime('%d/%m/%Y')}", text,
                   f"daily-{now.date().isoformat()}", "summary", None)

    def weekly_summary(self, now: datetime) -> None:
        if now.weekday() != int(self.db.get_setting("weekly_summary_weekday")) or \
                now.hour < int(self.db.get_setting("daily_summary_hour")):
            return
        iso = now.isocalendar()
        text, meaningful = self.summary_text(utc_ago(7), "Últimos 7 días")
        last_monday = (now.date() - timedelta(days=now.weekday() + 7)).isoformat()
        goals = self.db.query_one("SELECT COUNT(*) n, COALESCE(SUM(done),0) d FROM goals WHERE week_start=?", (last_monday,))
        if goals and goals["n"]:
            text += f" · Objetivos de la semana pasada: {goals['d']}/{goals['n']}"
        elif not meaningful:
            return
        with self.db.tx() as c:
            notify(self.db, c, all_users(c), "weekly_summary", f"Resumen semanal · semana {iso[1]}", text,
                   f"weekly-{iso[0]}-{iso[1]}", "summary", None)

    _ = parse_iso
