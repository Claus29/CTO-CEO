"""Métricas del equipo y búsqueda global (Ctrl+K)."""
from __future__ import annotations

import re
import statistics
from datetime import date, timedelta

from . import auth
from .common import clean_date, names, project_clause, visible_project_ids
from .db import parse_iso
from .web import bad_request


def hours_between(a: str | None, b: str | None) -> float | None:
    if not a or not b:
        return None
    return max(0.0, (parse_iso(b) - parse_iso(a)).total_seconds() / 3600)


def stats(values: list[float]) -> dict:
    vals = [v for v in values if v is not None]
    if not vals:
        return {"count": 0, "avg_hours": None, "median_hours": None}
    return {"count": len(vals), "avg_hours": round(sum(vals) / len(vals), 1),
            "median_hours": round(statistics.median(vals), 1)}


def register(router, app):
    db = app.db

    @router.route("GET", "/api/reports", perm="informes.ver")
    def reports(req):
        start = clean_date(req.q("from"), "desde") or (date.today() - timedelta(days=90)).isoformat()
        end = clean_date(req.q("to"), "hasta") or date.today().isoformat()
        end_ts = end + "T23:59:59.999Z"
        pclause, params = project_clause(db, req.user, "i.project_id", req)
        tclause, tparams = project_clause(db, req.user, "t.project_id", req)
        sla_rules = app.settings_cache("sla") or {}

        inc = db.query(f"SELECT i.* FROM incidents i WHERE {pclause} AND i.created_at BETWEEN ? AND ?",
                       params + [start, end_ts])
        first_response = [hours_between(r["created_at"], r["first_response_at"]) for r in inc]
        res = db.query(f"SELECT r.*, i.created_at AS inc_created, i.priority, i.reopen_count FROM resolutions r JOIN "
                       f"incidents i ON i.id = r.entity_id WHERE r.entity_type='incident' AND {pclause} AND "
                       f"r.resolved_at BETWEEN ? AND ?", params + [start, end_ts])
        verified = [r for r in res if r["state"] == "verificada"]
        rejected = [r for r in res if r["state"] == "rechazada"]
        lead = [hours_between(r["inc_created"], r["verified_at"] or r["resolved_at"]) for r in verified]
        to_verify = [hours_between(r["resolved_at"], r["verified_at"]) for r in verified if r["verified_by"] != r["resolved_by"]]
        reopened = sum(1 for r in verified if r["reopened_at"])

        # Cumplimiento de SLA
        sla_ok = sla_total = resp_ok = resp_total = 0
        for r in inc:
            rule = sla_rules.get(r["priority"]) or {}
            if rule.get("response_hours") and r["first_response_at"]:
                resp_total += 1
                resp_ok += hours_between(r["created_at"], r["first_response_at"]) <= rule["response_hours"]
        for r in verified:
            rule = sla_rules.get(r["priority"]) or {}
            if rule.get("resolve_hours"):
                sla_total += 1
                sla_ok += (r["duration_seconds"] or 0) / 3600 <= rule["resolve_hours"]

        tasks = db.query(f"SELECT t.* FROM tasks t WHERE {tclause} AND t.status='resuelto' AND t.completed_at BETWEEN ? AND ?",
                         tparams + [start, end_ts])
        cycle = [hours_between(t["started_at"], t["completed_at"]) for t in tasks if t["started_at"]]
        task_rej = db.scalar(f"SELECT COUNT(*) FROM resolutions r JOIN tasks t ON t.id=r.entity_id WHERE "
                             f"r.entity_type='task' AND r.state='rechazada' AND {tclause} AND r.resolved_at BETWEEN ? AND ?",
                             tparams + [start, end_ts]) or 0
        task_ver = db.scalar(f"SELECT COUNT(*) FROM resolutions r JOIN tasks t ON t.id=r.entity_id WHERE "
                             f"r.entity_type='task' AND r.state='verificada' AND {tclause} AND r.resolved_at BETWEEN ? AND ?",
                             tparams + [start, end_ts]) or 0

        # Ritmo semanal (12 semanas)
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        weeks = []
        for k in range(11, -1, -1):
            ws = monday - timedelta(weeks=k)
            we = ws + timedelta(days=7)
            t_done = db.scalar(f"SELECT COUNT(*) FROM tasks t WHERE {tclause} AND t.completed_at >= ? AND t.completed_at < ?",
                               tparams + [ws.isoformat(), we.isoformat()]) or 0
            i_done = db.scalar(f"SELECT COUNT(*) FROM incidents i WHERE {pclause} AND i.status='resuelta' AND "
                               f"i.resolved_at >= ? AND i.resolved_at < ?", params + [ws.isoformat(), we.isoformat()]) or 0
            i_new = db.scalar(f"SELECT COUNT(*) FROM incidents i WHERE {pclause} AND i.created_at >= ? AND i.created_at < ?",
                              params + [ws.isoformat(), we.isoformat()]) or 0
            weeks.append({"week": ws.isoformat(), "tasks_done": t_done, "incidents_resolved": i_done,
                          "incidents_created": i_new})

        # Errores por versión
        per_version = db.query(
            f"SELECT v.name, v.id, (SELECT COUNT(*) FROM incidents i WHERE i.found_build_id IN (SELECT id FROM builds WHERE "
            f"version_id=v.id)) AS found, (SELECT COUNT(*) FROM incidents i WHERE i.fixed_build_id IN (SELECT id FROM builds "
            f"WHERE version_id=v.id)) AS fixed FROM versions v WHERE {pclause.replace('i.project_id', 'v.project_id')} "
            f"ORDER BY v.id DESC LIMIT 12", params)

        # Por persona: carga, tiempo registrado y estimado frente a real
        un = names(db.conn)
        people = []
        for u in db.query("SELECT id, display_name FROM users WHERE active=1"):
            uid = u["id"]
            logged = db.scalar("SELECT COALESCE(SUM(seconds), 0) FROM time_entries WHERE user_id=? AND started_at BETWEEN ? "
                               "AND ?", (uid, start, end_ts)) or 0
            done_tasks = [t for t in tasks if t["assignee_id"] == uid]
            est = sum(t["estimate_hours"] or 0 for t in done_tasks)
            actual = 0
            for t in done_tasks:
                actual += db.scalar("SELECT COALESCE(SUM(seconds), 0) FROM time_entries WHERE entity_type='task' AND "
                                    "entity_id=?", (t["id"],)) or 0
            open_items = db.scalar("SELECT (SELECT COUNT(*) FROM tasks WHERE assignee_id=? AND status<>'resuelto') + "
                                   "(SELECT COUNT(*) FROM incidents WHERE assignee_id=? AND status IN ('pendiente',"
                                   "'en_progreso','bloqueada','en_verificacion'))", (uid, uid))
            people.append({"user_id": uid, "user_name": un.get(uid), "logged_hours": round(logged / 3600, 1),
                           "tasks_done": len(done_tasks), "estimated_hours": round(est, 1),
                           "actual_hours": round(actual / 3600, 1), "open_items": open_items,
                           "incidents_resolved": sum(1 for r in verified if r["resolved_by"] == uid)})

        return {
            "range": {"from": start, "to": end},
            "incidents": {"created": len(inc), "resolved": len(verified), "rejected": len(rejected),
                          "reopened": reopened, "reopen_rate": round(100 * reopened / len(verified)) if verified else None,
                          "rejection_rate": round(100 * len(rejected) / (len(verified) + len(rejected)))
                          if (verified or rejected) else None,
                          "first_response": stats(first_response), "lead_time": stats(lead),
                          "time_to_verification": stats(to_verify)},
            "sla": {"response_ok": resp_ok, "response_total": resp_total,
                    "response_rate": round(100 * resp_ok / resp_total) if resp_total else None,
                    "resolve_ok": sla_ok, "resolve_total": sla_total,
                    "resolve_rate": round(100 * sla_ok / sla_total) if sla_total else None},
            "tasks": {"done": len(tasks), "cycle_time": stats(cycle), "rejected": task_rej,
                      "rejection_rate": round(100 * task_rej / (task_rej + task_ver)) if (task_rej + task_ver) else None},
            "weekly": weeks, "per_version": per_version, "people": people,
        }

    # -- búsqueda global ---------------------------------------------------------------------
    KEY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9]{0,7})-(\d+)$")

    @router.route("GET", "/api/search")
    def search(req):
        q = (req.q("q") or "").strip()
        if not q:
            return {"items": []}
        if len(q) > 200:
            raise bad_request("Búsqueda demasiado larga.")
        user = req.user
        limit = min(req.q_int("limit", 20), 50)
        ids = visible_project_ids(db, user)
        perms = user["permissions"]
        allowed_kinds = {k for k, p in (("incident", "incidencias.ver"), ("task", "tareas.ver"), ("idea", "planificacion.ver"),
                                        ("wiki", "conocimiento.ver"), ("decision", "conocimiento.ver"),
                                        ("message", "chat.usar")) if p in perms}
        items = []
        m = KEY_RE.match(q)
        if m:
            for kind, table in (("incident", "incidents"), ("task", "tasks"), ("decision", "decisions")):
                if kind not in allowed_kinds:
                    continue
                row = db.query_one(f"SELECT id, key, title, project_id, status FROM {table} WHERE key=? COLLATE NOCASE",
                                   (f"{m.group(1).upper()}-{int(m.group(2)):03d}",)) or \
                    db.query_one(f"SELECT id, key, title, project_id, status FROM {table} WHERE key=? COLLATE NOCASE",
                                 (q.upper(),))
                if row and (ids is None or row["project_id"] in ids):
                    items.append({"type": kind, "id": row["id"], "key": row["key"], "title": row["title"],
                                  "status": row["status"], "snippet": "", "exact": True})
        words = [w for w in re.findall(r"[\wáéíóúüñÁÉÍÓÚÜÑ]+", q) if w]
        if words:
            match = " ".join(f'"{w}"' for w in words[:-1]) + (" " if len(words) > 1 else "") + f'"{words[-1]}"*'
            try:
                rows = db.query("SELECT kind, ref_id, project_id, key, title, snippet(search_index, 5, '[', ']', '…', 12) "
                                "AS snip, bm25(search_index) AS score FROM search_index WHERE search_index MATCH ? "
                                "ORDER BY score LIMIT 200", (match,))
            except Exception:
                rows = []
            channel_ok = None
            if "message" in allowed_kinds:
                channel_ok = {c["id"] for c in db.query("SELECT id, project_id FROM channels WHERE archived=0")
                              if c["project_id"] is None or ids is None or c["project_id"] in ids}
            seen = {(i["type"], i["id"]) for i in items}
            for r in rows:
                if r["kind"] not in allowed_kinds or (r["kind"], r["ref_id"]) in seen:
                    continue
                if r["project_id"] is not None and ids is not None and r["project_id"] not in ids:
                    continue
                extra = {}
                if r["kind"] == "message":
                    msg = db.query_one("SELECT channel_id, user_id FROM messages WHERE id=?", (r["ref_id"],))
                    if not msg or msg["channel_id"] not in channel_ok:
                        continue
                    extra = {"channel_id": msg["channel_id"]}
                items.append({"type": r["kind"], "id": r["ref_id"], "key": r["key"], "title": r["title"],
                              "snippet": r["snip"], **extra})
                if len(items) >= limit:
                    break
        people = db.query("SELECT id, display_name, username FROM users WHERE active=1 AND (display_name LIKE ? OR "
                          "username LIKE ?) LIMIT 5", (f"%{q}%", f"%{q}%"))
        for p in people:
            items.append({"type": "person", "id": p["id"], "title": p["display_name"], "snippet": f"@{p['username']}"})
        return {"items": items[:limit + 5]}

    _ = auth
