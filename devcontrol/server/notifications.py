"""Notificaciones, preferencias por usuario y avisos programados (sin duplicados)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .common import NOTIFICATION_AREAS, NOTIFICATION_TYPES, OPEN_INCIDENT, all_users, clean_int, notify, users_with_perm
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
        from .watch import CHANNEL_MODES, TASK_SCOPES, settings_for
        with db.tx() as c:
            settings = settings_for(c, uid)
        return {"items": items, "mute_until": db.scalar("SELECT mute_until FROM users WHERE id=?", (uid,)),
                "areas": [{"label": a, "types": ts} for a, ts in NOTIFICATION_AREAS.items()],
                "settings": settings, "task_scopes": TASK_SCOPES, "channel_modes": CHANNEL_MODES,
                "presets": {"todo": "Todo", "importante": "Lo importante", "solo_mio": "Solo lo mío"}}

    app.notification_prefs = prefs_for

    @router.route("GET", "/api/notifications/prefs")
    def get_prefs(req):
        return prefs_for(req.user["id"])

    @router.route("PUT", "/api/notifications/prefs")
    def put_prefs(req):
        data = req.json
        uid = req.user["id"]
        with db.tx() as c:
            if data.get("preset"):
                if data["preset"] not in ("todo", "importante", "solo_mio"):
                    raise bad_request("Ajuste rápido no válido.")
                app.notify_preset(c, uid, data["preset"])
            if isinstance(data.get("settings"), dict):
                app.notify_settings_save(c, uid, data["settings"])
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

    # Después de /prefs: si no, «prefs» se tomaría por el número de un aviso.
    @router.route("GET", "/api/notifications/{nid}")
    def one_notification(req, nid):
        row = db.query_one("SELECT * FROM notifications WHERE id=? AND user_id=?", (nid, req.user["id"]))
        if not row:
            from .web import not_found
            raise not_found("El aviso no existe.")
        return {"item": row}


class NotificationJobs:
    """Avisos que no dependen de una acción concreta. Cada uno lleva su clave de deduplicación."""

    def __init__(self, app):
        self.app = app
        self.db = app.db

    def run(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        for job in (self.due_dates, self.stale, self.sla, self.renewals, self.standup, self.daily_summary,
                    self.weekly_summary, self.pending_digest, self.verify_waiting, self.calendar, self.absences,
                    self.version_risk, self.disk_space):
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

    # -- 2.2 ------------------------------------------------------------------------------------------
    def pending_digest(self, now: datetime) -> None:
        """«Tus pendientes»: cada día laborable, a la hora que elige cada persona (09:00 por defecto)."""
        if now.weekday() >= 5:
            return
        day, hhmm = now.date().isoformat(), now.strftime("%H:%M")
        with self.db.tx() as c:
            times = {r[0]: r[1] for r in c.execute("SELECT user_id, digest_time FROM notify_settings")}
            for uid in all_users(c):
                if hhmm < (times.get(uid) or "09:00"):
                    continue
                if c.execute("SELECT 1 FROM notifications WHERE user_id=? AND dedupe_key=?",
                             (uid, f"digest-{day}")).fetchone():
                    continue
                parts = []
                tasks = c.execute("SELECT COUNT(*) FROM tasks WHERE assignee_id=? AND status IN ('pendiente',"
                                  "'en_progreso','bloqueada')", (uid,)).fetchone()[0]
                incs = c.execute("SELECT COUNT(*) FROM incidents WHERE assignee_id=? AND status IN ('pendiente',"
                                 "'en_progreso','bloqueada')", (uid,)).fetchone()[0]
                if tasks or incs:
                    what = " y ".join(x for x in (f"{tasks} tarea{'s' if tasks != 1 else ''}" if tasks else "",
                                                   f"{incs} incidencia{'s' if incs != 1 else ''}" if incs else "") if x)
                    parts.append(f"Tienes asignada{'s' if tasks + incs != 1 else ''} {what}")
                verify = c.execute("SELECT (SELECT COUNT(*) FROM tasks WHERE created_by=? AND status='en_verificacion')"
                                   " + (SELECT COUNT(*) FROM incidents WHERE created_by=? AND "
                                   "status='en_verificacion')", (uid, uid)).fetchone()[0]
                if verify:
                    parts.append(f"{verify} espera{'n' if verify != 1 else ''} tu verificación")
                due = c.execute("SELECT (SELECT COUNT(*) FROM tasks WHERE assignee_id=? AND status IN ('pendiente',"
                                "'en_progreso','bloqueada') AND target_date <= ?) + (SELECT COUNT(*) FROM incidents "
                                "WHERE assignee_id=? AND status IN ('pendiente','en_progreso','bloqueada') AND "
                                "target_date <= ?)", (uid, day, uid, day)).fetchone()[0]
                if due:
                    parts.append(f"{due} vence{'n' if due != 1 else ''} hoy o ya ha{'n' if due != 1 else ''} vencido")
                if parts:
                    notify(self.db, c, [uid], "pending_digest", "Tus pendientes de hoy", " · ".join(parts),
                           f"digest-{day}", "pending", None, urgent=bool(due))

    def verify_waiting(self, now: datetime) -> None:
        """Lo que lleva más de un día esperando verificación: recordatorio diario a quien debe verificarlo."""
        day = now.date().isoformat()
        limit = utc_ago(1)
        with self.db.tx() as c:
            for etype, table, perm in (("task", "tasks", "tareas.verificar_todas"),
                                       ("incident", "incidents", "incidencias.verificar_todas")):
                rows = c.execute(f"SELECT id, key, title, created_by, project_id FROM {table} WHERE "
                                 f"status='en_verificacion' AND verification_requested_at < ?", (limit,)).fetchall()
                for r in rows:
                    notify(self.db, c, {r["created_by"]} | set(users_with_perm(c, perm, r["project_id"])),
                           "verify_waiting", f"{r['key']} lleva más de un día esperando tu verificación", r["title"],
                           f"verify-wait-{etype}-{r['id']}-{day}", etype, r["id"], r["key"])

    def calendar(self, now: datetime) -> None:
        if now.hour < 8:
            return
        today = now.date()
        with self.db.tx() as c:
            for when, label in ((today, "Hoy"), (today + timedelta(days=1), "Mañana")):
                for r in c.execute("SELECT id, title, project_id, description FROM project_dates WHERE date=?",
                                   (when.isoformat(),)).fetchall():
                    notify(self.db, c, users_with_perm(c, "planificacion.ver", r["project_id"]), "calendar",
                           f"{label}: {r['title']}", r["description"][:200], f"date-{r['id']}-{label}", "date", r["id"])

    def absences(self, now: datetime) -> None:
        if now.hour < 9:
            return
        tomorrow = (now.date() + timedelta(days=1)).isoformat()
        with self.db.tx() as c:
            for r in c.execute("SELECT a.id, a.user_id, a.end_date, u.display_name FROM absences a JOIN users u "
                               "ON u.id=a.user_id WHERE a.start_date=?", (tomorrow,)).fetchall():
                mates = {x[0] for x in c.execute(
                    "SELECT DISTINCT pm2.user_id FROM project_members pm1 JOIN project_members pm2 "
                    "ON pm2.project_id=pm1.project_id WHERE pm1.user_id=?", (r["user_id"],))}
                mates.discard(r["user_id"])
                until = "" if r["end_date"] == tomorrow else f" (hasta el {r['end_date']})"
                notify(self.db, c, mates, "team_absence", f"Mañana no está {r['display_name']}{until}",
                       "Revisa si tiene algo asignado que necesites.", f"absence-tomorrow-{r['id']}", "absence", r["id"])

    def version_risk(self, now: datetime) -> None:
        today = now.date()
        soon = (today + timedelta(days=3)).isoformat()
        with self.db.tx() as c:
            for v in c.execute("SELECT id, name, project_id, target_date, created_by FROM versions WHERE "
                               "status<>'publicada' AND target_date BETWEEN ? AND ?",
                               (today.isoformat(), soon)).fetchall():
                open_ = c.execute("SELECT COUNT(*) FROM tasks WHERE version_id=? AND status<>'resuelto'",
                                  (v["id"],)).fetchone()[0]
                if open_:
                    notify(self.db, c, {v["created_by"]} | set(users_with_perm(c, "planificacion.editar",
                                                                                v["project_id"])), "release",
                           f"La versión {v['name']} sale el {v['target_date']} y tiene {open_} tareas abiertas",
                           "Revisa el roadmap: quizá haya que mover tareas o la fecha.",
                           f"version-risk-{v['id']}-{v['target_date']}", "version", v["id"])

    def disk_space(self, now: datetime) -> None:
        import shutil
        folder = getattr(self.app, "data_dir", None)
        if folder is None:
            return
        try:
            free = shutil.disk_usage(folder).free
        except OSError:
            return
        if free < 2 * 1024 ** 3:
            with self.db.tx() as c:
                notify(self.db, c, users_with_perm(c, "sistema.copias"), "server_health",
                       "Queda poco espacio en el disco del anfitrión",
                       f"Libres: {free / 1024 ** 3:.1f} GB. Las copias de seguridad pueden fallar.",
                       f"disk-low-{now.date().isoformat()}", urgent=True)

    _ = parse_iso
