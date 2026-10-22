"""Incidencias: el módulo principal (ID por proyecto, verificación, plantillas, duplicados y SLA)."""
from __future__ import annotations

import json
from datetime import date, timedelta

from . import auth
from .common import (
    BLOCK_REASONS,
    CATEGORIES,
    ENVIRONMENTS,
    FREQUENCIES,
    INCIDENT_STATUSES,
    LIST_PREVIEW_CHARS,
    LONG_TEXT_MAX,
    OPEN_INCIDENT,
    PRIORITIES,
    REPORT_FIELDS,
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
)
from .db import now_iso, parse_iso, seconds_between
from .entities import find_entity
from .web import HttpError, Response, bad_request, forbidden

FIELDS = {
    "title": lambda v: clean_text(v, "título", 200, required=True),
    "description": lambda v: clean_text(v, "descripción", LONG_TEXT_MAX),
    "priority": lambda v: clean_choice(v, "prioridad", PRIORITIES),
    "category": lambda v: clean_choice(v, "categoría", CATEGORIES),
    "steps": lambda v: clean_text(v, "pasos", 10000),
    "expected": lambda v: clean_text(v, "resultado esperado", 5000),
    "observed": lambda v: clean_text(v, "resultado observado", 5000),
    "device": lambda v: clean_text(v, "dispositivo", 120),
    "os_version": lambda v: clean_text(v, "sistema y versión", 120),
    "app_version": lambda v: clean_text(v, "versión de la app", 60),
    "environment": lambda v: clean_choice(v, "entorno", ENVIRONMENTS, ""),
    "frequency": lambda v: clean_choice(v, "frecuencia", FREQUENCIES, ""),
    "target_date": lambda v: clean_date(v, "fecha objetivo"),
    "labels": clean_labels,
    "estimate_hours": lambda v: clean_float(v, "estimación (horas)"),
}
FIELD_LABELS = {"title": "título", "description": "descripción", "priority": "prioridad", "category": "categoría",
                "steps": "pasos para reproducir", "expected": "resultado esperado", "observed": "resultado observado",
                "device": "dispositivo", "os_version": "sistema", "app_version": "versión de la app",
                "environment": "entorno", "frequency": "frecuencia", "target_date": "fecha objetivo",
                "labels": "etiquetas", "estimate_hours": "estimación"}


def format_key(project_key: str, number: int) -> str:
    return f"{project_key}-{number:03d}"


def sla_info(row: dict, sla_settings: dict, enabled: bool) -> dict | None:
    if not enabled or row["status"] not in OPEN_INCIDENT:
        return None
    rules = sla_settings.get(row["priority"]) or {}
    if not rules:
        return None
    now = parse_iso(now_iso())
    out = {}
    start = parse_iso(row["created_at"])
    if not row.get("first_response_at") and rules.get("response_hours"):
        due = start + timedelta(hours=rules["response_hours"])
        out["response_due"] = due.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        out["response_breached"] = now > due
    if rules.get("resolve_hours"):
        due = parse_iso(row["cycle_started_at"]) + timedelta(hours=rules["resolve_hours"])
        out["resolve_due"] = due.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        out["resolve_breached"] = now > due
        total = rules["resolve_hours"] * 3600
        used = (now - parse_iso(row["cycle_started_at"])).total_seconds()
        out["resolve_ratio"] = round(used / total, 2) if total else 0
    out["state"] = "vencido" if out.get("response_breached") or out.get("resolve_breached") else \
        ("riesgo" if out.get("resolve_ratio", 0) >= 0.75 else "ok")
    return out


def register(router, app):
    db = app.db

    def serialize(row: dict, user_names: dict, projects: dict, extra: dict | None = None) -> dict:
        today = date.today().isoformat()
        out = dict(row)
        out["priority_label"] = PRIORITIES.get(row["priority"], row["priority"])
        out["category_label"] = CATEGORIES.get(row["category"], row["category"])
        out["status_label"] = INCIDENT_STATUSES.get(row["status"], row["status"])
        out["block_reason_label"] = BLOCK_REASONS.get(row["block_reason_code"]) if row["block_reason_code"] else None
        for col in ("created_by", "updated_by", "resolved_by", "blocked_by", "assignee_id",
                    "verification_requested_by"):
            out[col.replace("_id", "") + "_name" if col.endswith("_id") else col + "_name"] = \
                user_names.get(row[col]) if row.get(col) else None
        p = projects.get(row["project_id"]) or {}
        out["project_key"] = p.get("key")
        out["project_name"] = p.get("name")
        out["project_color"] = p.get("color")
        out["labels_list"] = [x for x in (row.get("labels") or "").split(",") if x]
        out["na_fields"] = json.loads(row.get("na_fields") or "{}")
        out["version"] = row["row_version"]
        out["overdue"] = bool(row["target_date"] and row["status"] in OPEN_INCIDENT and row["target_date"] < today)
        end = row["resolved_at"] if row["status"] in ("resuelta", "duplicada") and row["resolved_at"] else now_iso()
        out["current_cycle_seconds"] = seconds_between(row["cycle_started_at"], end)
        out["sla"] = sla_info(row, app.settings_cache("sla"), app.settings_cache("sla_enabled"))
        if extra:
            out.update(extra)
        return out

    app.serialize_incident = serialize

    def projects_map() -> dict:
        return {r["id"]: r for r in db.query("SELECT id, key, name, color FROM projects")}

    def counts_for(ids: list[int]):
        if not ids:
            return {}, {}, {}
        marks = ",".join("?" * len(ids))
        att = {r["owner_id"]: r["n"] for r in db.query(
            f"SELECT owner_id, COUNT(*) n FROM attachments WHERE owner_type='incident' AND owner_id IN ({marks}) "
            "GROUP BY owner_id", ids)}
        com = {r["entity_id"]: r["n"] for r in db.query(
            f"SELECT entity_id, COUNT(*) n FROM comments WHERE entity_type='incident' AND entity_id IN ({marks}) "
            "GROUP BY entity_id", ids)}
        seen = {}
        for r in db.query(f"SELECT incident_id, user_id, first_seen_at FROM incident_views WHERE incident_id IN "
                          f"({marks})", ids):
            seen.setdefault(r["incident_id"], []).append(r)
        return att, com, seen

    def build_filters(req, user) -> tuple[str, list]:
        pclause, params = project_clause(db, user, "i.project_id", req)
        where = [pclause]
        q = (req.q("q") or "").strip()
        if q:
            like = f"%{q}%"
            where.append("(i.title LIKE ? OR i.description LIKE ? OR i.key LIKE ? OR i.steps LIKE ? OR "
                         "i.observed LIKE ? OR i.labels LIKE ?)")
            params += [like] * 6
        for name, col, choices in (("status", "i.status", INCIDENT_STATUSES), ("priority", "i.priority", PRIORITIES),
                                   ("category", "i.category", CATEGORIES)):
            raw = req.q(name)
            if raw:
                vals = [v for v in raw.split(",") if v]
                for v in vals:
                    if v not in choices:
                        raise bad_request(f"Filtro no válido: {name}={v}")
                where.append(f"{col} IN ({','.join('?' * len(vals))})")
                params += vals
        if req.q("blocked") == "1":
            where.append("i.status = 'bloqueada'")
        if req.q("open") == "1":
            where.append(f"i.status IN ({','.join('?' * len(OPEN_INCIDENT))})")
            params += list(OPEN_INCIDENT)
        assignee = req.q("assignee")
        if assignee == "me":
            where.append("i.assignee_id = ?")
            params.append(user["id"])
        elif assignee == "none":
            where.append("i.assignee_id IS NULL")
        elif assignee:
            where.append("i.assignee_id = ?")
            params.append(clean_int(assignee, "responsable"))
        if req.q("to_verify") == "1":
            where.append("i.status = 'en_verificacion'")
            if "incidencias.verificar_todas" not in user["permissions"]:
                where.append("i.created_by = ?")
                params.append(user["id"])
        if req.q("label"):
            where.append("(',' || i.labels || ',') LIKE ?")
            params.append(f"%,{req.q('label').lower()},%")
        if req.q("seen") in ("0", "1"):
            sub = "EXISTS (SELECT 1 FROM incident_views v WHERE v.incident_id = i.id AND v.user_id <> i.created_by)"
            where.append(sub if req.q("seen") == "1" else f"NOT {sub}")
        if req.q("overdue") == "1":
            where.append(f"i.target_date IS NOT NULL AND i.target_date < ? AND i.status IN "
                         f"({','.join('?' * len(OPEN_INCIDENT))})")
            params += [date.today().isoformat(), *OPEN_INCIDENT]
        if req.q("resolved_since"):
            where.append("i.status = 'resuelta' AND i.resolved_at >= ?")
            params.append(req.q("resolved_since"))
        if req.q("version_id"):
            where.append("(i.fixed_build_id IN (SELECT id FROM builds WHERE version_id=?) OR "
                         "i.found_build_id IN (SELECT id FROM builds WHERE version_id=?))")
            vid = clean_int(req.q("version_id"), "versión")
            params += [vid, vid]
        for name, col, op in (("created_from", "i.created_at", ">="), ("created_to", "i.created_at", "<="),
                              ("target_from", "i.target_date", ">="), ("target_to", "i.target_date", "<=")):
            if req.q(name):
                where.append(f"{col} IS NOT NULL AND {col} {op} ?")
                params.append(req.q(name))
        return " WHERE " + " AND ".join(where), params

    ORDERS = {
        "priority": "CASE i.priority WHEN 'critica' THEN 0 WHEN 'alta' THEN 1 WHEN 'media' THEN 2 ELSE 3 END, "
                    "CASE WHEN i.status IN ('resuelta','duplicada') THEN 1 ELSE 0 END, i.created_at DESC",
        "created_desc": "i.created_at DESC", "created_asc": "i.created_at ASC", "updated": "i.updated_at DESC",
        "due": "CASE WHEN i.target_date IS NULL THEN 1 ELSE 0 END, i.target_date ASC, i.created_at ASC",
    }

    def list_incidents(req, user, limit: int | None = None) -> list[dict]:
        where, params = build_filters(req, user)
        order = ORDERS.get(req.q("sort") or "priority")
        if order is None:
            raise bad_request("Orden no válido.")
        sql = f"SELECT i.* FROM incidents i{where} ORDER BY {order}"
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = db.query(sql, params)
        user_names, projects = names(db.conn), projects_map()
        att, com, seen = counts_for([r["id"] for r in rows])
        out = []
        for r in rows:
            views = [v for v in seen.get(r["id"], []) if v["user_id"] != r["created_by"]]
            first = min(views, key=lambda v: v["first_seen_at"]) if views else None
            out.append(serialize(r, user_names, projects, {
                "attachments_count": att.get(r["id"], 0), "comments_count": com.get(r["id"], 0),
                "seen_at": first["first_seen_at"] if first else None,
                "seen_by_name": user_names.get(first["user_id"]) if first else None,
                "assignee_seen": any(v["user_id"] == r["assignee_id"] for v in views) if r["assignee_id"] else None,
            }))
        return out

    app.list_incidents = list_incidents

    @router.route("GET", "/api/incidents", perm="incidencias.ver")
    def get_list(req):
        items = list_incidents(req, req.user)
        for it in items:   # descripciones muy largas: en el listado solo el principio
            if len(it.get("description") or "") > LIST_PREVIEW_CHARS:
                it["description"] = it["description"][:LIST_PREVIEW_CHARS] + "…"
                it["description_truncated"] = True
        return {"items": items}

    @router.route("GET", "/api/incidents/similar", perm="incidencias.ver")
    def similar(req):
        """Posibles duplicados mientras se escribe el título."""
        text = (req.q("q") or "").strip()
        if len(text) < 4:
            return {"items": []}
        words = [w for w in "".join(ch if ch.isalnum() else " " for ch in text).split() if len(w) > 2][:8]
        if not words:
            return {"items": []}
        match = " OR ".join(f'"{w}"' for w in words)
        pclause, params = project_clause(db, req.user, "i.project_id")
        rows = db.query(
            f"SELECT i.id, i.key, i.title, i.status, i.priority, bm25(search_index) AS score FROM search_index s "
            f"JOIN incidents i ON i.id = s.ref_id WHERE s.kind='incident' AND search_index MATCH ? AND {pclause} "
            f"AND i.status NOT IN ('duplicada') ORDER BY score LIMIT 5", [f"title : ({match})"] + params)
        exclude = req.q("exclude")
        return {"items": [r for r in rows if str(r["id"]) != exclude and r["key"] != exclude]}

    def validate_template(fields: dict, na: dict) -> None:
        templates = app.settings_cache("report_templates") or {}
        tpl = templates.get(fields["category"]) or {}
        missing = [f for f in tpl.get("required", []) if not fields.get(f) and not (na.get(f) or "").strip()]
        if missing:
            raise HttpError(400, "reporte_incompleto",
                            "Faltan datos obligatorios para la categoría «" + CATEGORIES[fields["category"]] + "»: " +
                            ", ".join(REPORT_FIELDS.get(m, m) for m in missing) +
                            ". Rellénalos o márcalos como «no aplicable» indicando el motivo.",
                            {"missing": missing})

    def clean_na(value) -> dict:
        if not value:
            return {}
        if not isinstance(value, dict):
            raise bad_request("Campos no aplicables no válidos.")
        return {k: clean_text(v, f"motivo de «{k}» no aplicable", 300) for k, v in value.items()
                if k in REPORT_FIELDS and v}

    def build_exists(c, build_id, project_id) -> int | None:
        if build_id in (None, "", 0):
            return None
        bid = clean_int(build_id, "build")
        if not c.execute("SELECT 1 FROM builds WHERE id=? AND project_id=?", (bid, project_id)).fetchone():
            raise bad_request("La build indicada no pertenece al proyecto.")
        return bid

    def notify_new(c, iid, key, fields, user, project_id, assignee):
        critical = fields["priority"] == "critica"
        workers = users_with_perm(c, "incidencias.trabajar", project_id)
        notify(db, c, workers, "incident_critical" if critical else "incident_created",
               f"{'¡CRÍTICA! ' if critical else ''}Nueva incidencia {key}", fields["title"], f"inc-created-{iid}",
               "incident", iid, key, urgent=critical, exclude=user["id"])
        if assignee and assignee != user["id"]:
            notify(db, c, [assignee], "assigned", f"Te han asignado {key}", fields["title"], f"inc-assigned-{iid}-0",
                   "incident", iid, key, urgent=critical)

    @router.route("POST", "/api/incidents", perm="incidencias.crear", idempotent=True)
    def create(req):
        data = req.json
        uid = clean_uid(data.get("uid"))
        user = req.user
        existing = db.query_one("SELECT * FROM incidents WHERE uid=?", (uid,))
        if existing:
            return Response.json({"item": serialize(existing, names(db.conn), projects_map()), "duplicate": True})
        fields = {k: fn(data.get(k)) for k, fn in FIELDS.items()}
        na = clean_na(data.get("na_fields"))
        if not data.get("skip_template"):
            validate_template(fields, na)
        project = require_project(db, user, data.get("project_id") or default_project_id(db, user))
        if project["archived"]:
            raise bad_request("El proyecto está archivado.")
        with db.tx() as c:
            assignee = user_exists(c, data.get("assignee_id"))
            if assignee and assignee != user["id"]:
                auth.require(user, "incidencias.asignar")
            found = build_exists(c, data.get("found_build_id"), project["id"])
            number = db.next_counter(c, f"incident:{project['id']}")
            key = format_key(project["key"], number)
            ts = now_iso()
            cur = c.execute(
                "INSERT INTO incidents(uid, project_id, number, key, title, description, priority, category, steps, "
                "expected, observed, device, os_version, app_version, environment, frequency, na_fields, labels, "
                "target_date, estimate_hours, status, assignee_id, found_build_id, created_by, created_at, "
                "updated_at, updated_by, cycle_started_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
                "'pendiente',?,?,?,?,?,?,?)",
                (uid, project["id"], number, key, fields["title"], fields["description"], fields["priority"],
                 fields["category"], fields["steps"], fields["expected"], fields["observed"], fields["device"],
                 fields["os_version"], fields["app_version"], fields["environment"], fields["frequency"],
                 json.dumps(na, ensure_ascii=False), fields["labels"], fields["target_date"],
                 fields["estimate_hours"], assignee, found, user["id"], ts, ts, user["id"], ts))
            iid = cur.lastrowid
            log_activity(db, c, user, "incident", iid, key, "created", f"Creó la incidencia {key}: {fields['title']}",
                         {"priority": fields["priority"], "category": fields["category"]}, project["id"])
            notify_new(c, iid, key, fields, user, project["id"], assignee)
            notify(db, c, mentioned_users(c, fields["description"]), "mention",
                   f"{user['display_name']} te ha mencionado en {key}", fields["title"], f"mention-inc-{iid}",
                   "incident", iid, key, exclude=user["id"])
            db.index(c, "incident", iid, project["id"], key, fields["title"],
                     f"{fields['description']} {fields['steps']} {fields['observed']} {fields['labels']}")
            if data.get("source_message_id"):
                mid = clean_int(data["source_message_id"], "mensaje")
                if c.execute("SELECT 1 FROM messages WHERE id=?", (mid,)).fetchone():
                    c.execute("INSERT OR IGNORE INTO links(from_type, from_id, to_type, to_id, kind, created_by, "
                              "created_at) VALUES ('message', ?, 'incident', ?, 'origen', ?, ?)",
                              (mid, iid, user["id"], ts))
                    db.emit(c, "message", "message", mid, actor_id=user["id"])
            db.emit(c, "incident", "incident", iid, actor_id=user["id"])
        row = db.query_one("SELECT * FROM incidents WHERE id=?", (iid,))
        return Response.json({"item": serialize(row, names(db.conn), projects_map())}, 201)

    @router.route("GET", "/api/incidents/{ident}", perm="incidencias.ver")
    def detail(req, ident):
        row = find_entity(db, req.user, "incident", ident)
        user_names = names(db.conn)
        iid = row["id"]
        detail_extra = app.entity_extras("incident", iid, req.user)
        resolutions = db.query("SELECT * FROM resolutions WHERE entity_type='incident' AND entity_id=? ORDER BY id",
                               (iid,))
        for r in resolutions:
            r["resolved_by_name"] = user_names.get(r["resolved_by"])
            r["verified_by_name"] = user_names.get(r["verified_by"]) if r["verified_by"] else None
            r["reopened_by_name"] = user_names.get(r["reopened_by"]) if r["reopened_by"] else None
        total = sum(r["duration_seconds"] or 0 for r in resolutions if r["state"] == "verificada")
        if row["status"] in OPEN_INCIDENT:
            total += seconds_between(row["cycle_started_at"], now_iso())
        views = db.query("SELECT user_id, first_seen_at FROM incident_views WHERE incident_id=? ORDER BY first_seen_at",
                         (iid,))
        for v in views:
            v["user_name"] = user_names.get(v["user_id"])
        duplicates = db.query("SELECT id, key, title, status FROM incidents WHERE duplicate_of_id=?", (iid,))
        dup_of = db.query_one("SELECT id, key, title, status FROM incidents WHERE id=?", (row["duplicate_of_id"],)) \
            if row["duplicate_of_id"] else None
        builds = {b["id"]: b for b in db.query("SELECT id, number, platform FROM builds WHERE id IN (?, ?)",
                                               (row["found_build_id"] or 0, row["fixed_build_id"] or 0))}
        can_verify = row["status"] == "en_verificacion" and (
            row["created_by"] == req.user["id"] or "incidencias.verificar_todas" in req.user["permissions"])
        return {"item": serialize(row, user_names, projects_map(), {
            **detail_extra, "resolutions": resolutions, "total_open_seconds": total, "views": views,
            "duplicates": duplicates, "duplicate_of": dup_of, "can_verify": can_verify,
            "found_build": builds.get(row["found_build_id"]), "fixed_build": builds.get(row["fixed_build_id"]),
        })}

    @router.route("PATCH", "/api/incidents/{ident}", idempotent=True)
    def edit(req, ident):
        data = req.json
        user = req.user
        if data.get("version") is None:
            raise bad_request("Falta la versión de la incidencia (control de cambios simultáneos).")
        with db.tx() as c:
            row = find_entity(db, user, "incident", ident)
            check_version(row, data, f"La incidencia {row['key']}")
            changes = {}
            field_keys = [k for k in FIELDS if k in data]
            if field_keys:
                if not (auth.has_perm(user, "incidencias.editar") or row["created_by"] == user["id"]):
                    raise forbidden("Solo quien la creó o quien tiene permiso de edición puede cambiar sus campos.")
                for k in field_keys:
                    val = FIELDS[k](data[k])
                    if val != row[k]:
                        changes[k] = (row[k], val)
            if "na_fields" in data:
                na = json.dumps(clean_na(data["na_fields"]), ensure_ascii=False)
                if na != row["na_fields"]:
                    changes["na_fields"] = (row["na_fields"], na)
            if "assignee_id" in data:
                new_assignee = user_exists(c, data["assignee_id"])
                if new_assignee != row["assignee_id"]:
                    if not (auth.has_perm(user, "incidencias.asignar") or
                            (new_assignee == user["id"] and auth.has_perm(user, "incidencias.trabajar"))):
                        raise forbidden("No tienes permiso para asignar incidencias a otras personas.")
                    changes["assignee_id"] = (row["assignee_id"], new_assignee)
            for col, _label in (("found_build_id", "build en la que se encontró"),
                               ("fixed_build_id", "build en la que se corrigió")):
                if col in data:
                    auth.require_any(user, "incidencias.editar", "incidencias.trabajar")
                    val = build_exists(c, data[col], row["project_id"])
                    if val != row[col]:
                        changes[col] = (row[col], val)
            if not changes:
                return {"item": serialize(row, names(c), projects_map()), "unchanged": True}
            ts = now_iso()
            sets = ", ".join(f"{k}=?" for k in changes)
            c.execute(f"UPDATE incidents SET {sets}, updated_at=?, updated_by=?, row_version=row_version+1 WHERE id=?",
                      [v[1] for v in changes.values()] + [ts, user["id"], row["id"]])
            key = row["key"]
            un = names(c)
            for k, (old, new) in changes.items():
                if k == "priority":
                    summary = f"Cambió la prioridad de {key}: {PRIORITIES[old]} → {PRIORITIES[new]}"
                elif k == "assignee_id":
                    summary = f"Asignó {key} a {un.get(new, 'nadie')}" if new else f"Quitó el responsable de {key}"
                    if new and new != user["id"]:
                        notify(db, c, [new], "assigned", f"Te han asignado {key}", row["title"],
                               f"inc-assigned-{row['id']}-{row['row_version']}", "incident", row["id"], key,
                               urgent=row["priority"] == "critica")
                elif k == "category":
                    summary = f"Cambió la categoría de {key}: {CATEGORIES[old]} → {CATEGORIES[new]}"
                elif k == "target_date":
                    summary = f"Cambió la fecha objetivo de {key}: {old or 'sin fecha'} → {new or 'sin fecha'}"
                elif k == "title":
                    summary = f"Cambió el título de {key} a «{new}»"
                elif k in ("found_build_id", "fixed_build_id"):
                    summary = f"Indicó la build {'en la que se encontró' if k == 'found_build_id' else 'de la corrección'} " \
                              f"de {key}"
                elif k == "na_fields":
                    summary = f"Actualizó los campos no aplicables de {key}"
                else:
                    summary = f"Editó «{FIELD_LABELS.get(k, k)}» de {key}"
                    if k in ("description", "steps", "expected", "observed"):
                        c.execute("INSERT INTO edits(entity_type, entity_id, previous_body, edited_at, edited_by) "
                                  "VALUES ('incident', ?, ?, ?, ?)",
                                  (row["id"], f"[{FIELD_LABELS[k]}]\n{old}", ts, user["id"]))
                log_activity(db, c, user, "incident", row["id"], key, f"edit_{k}", summary,
                             {"field": k} if k in ("description", "steps", "na_fields") else
                             {"field": k, "old": old, "new": new}, row["project_id"])
            if "priority" in changes and changes["priority"][1] == "critica" and row["status"] in OPEN_INCIDENT:
                notify(db, c, users_with_perm(c, "incidencias.trabajar", row["project_id"]), "incident_critical",
                       f"¡CRÍTICA! {key} ahora es crítica", row["title"],
                       f"inc-critical-{row['id']}-{row['row_version'] + 1}", "incident", row["id"], key, urgent=True,
                       exclude=user["id"])
            new = dict(c.execute("SELECT * FROM incidents WHERE id=?", (row["id"],)).fetchone())
            db.index(c, "incident", new["id"], new["project_id"], key, new["title"],
                     f"{new['description']} {new['steps']} {new['observed']} {new['labels']}")
            db.emit(c, "incident", "incident", row["id"], actor_id=user["id"])
            return {"item": serialize(new, names(c), projects_map())}

    def mark_first_response(c, row, user):
        if not row["first_response_at"] and user["id"] != row["created_by"]:
            c.execute("UPDATE incidents SET first_response_at=? WHERE id=?", (now_iso(), row["id"]))

    def finalize_resolution(c, row, user, res_id, ts):
        """Resolución aceptada: se cierra la incidencia y se desbloquea lo que dependía de ella."""
        res = c.execute("SELECT * FROM resolutions WHERE id=?", (res_id,)).fetchone()
        duration = seconds_between(row["cycle_started_at"], ts)
        c.execute("UPDATE resolutions SET state='verificada', verified_by=?, verified_at=?, duration_seconds=? "
                  "WHERE id=?", (user["id"], ts, duration, res_id))
        c.execute("UPDATE incidents SET status='resuelta', resolved_at=?, resolved_by=?, block_reason_code=NULL, "
                  "block_reason_text=NULL, blocked_at=NULL, blocked_by=NULL, verification_requested_at=NULL, "
                  "verification_requested_by=NULL, updated_at=?, updated_by=?, row_version=row_version+1 WHERE id=?",
                  (ts, res["resolved_by"], ts, user["id"], row["id"]))
        app.unblock_dependents(c, "incident", row["id"], row["key"], user)
        return duration

    @router.route("POST", "/api/incidents/{ident}/status", perm="incidencias.ver", idempotent=True)
    def change_status(req, ident):
        data = req.json
        user = req.user
        new_status = clean_choice(data.get("status"), "estado", INCIDENT_STATUSES)
        with db.tx() as c:
            row = find_entity(db, user, "incident", ident)
            check_version(row, data, f"La incidencia {row['key']}", required=False)
            old, key, iid, pid = row["status"], row["key"], row["id"], row["project_id"]
            ts = now_iso()
            if old == new_status and new_status != "bloqueada":
                raise bad_request(f"{key} ya está en estado «{INCIDENT_STATUSES[old]}».", "sin_cambios")
            if new_status in ("en_verificacion", "duplicada"):
                raise bad_request("Usa la acción correspondiente (resolver o marcar como duplicada).")

            if new_status == "resuelta":
                auth.require(user, "incidencias.resolver")
                if old == "en_verificacion":
                    raise bad_request(f"{key} está pendiente de verificación: verifícala o recházala.")
                if old in ("resuelta", "duplicada"):
                    raise bad_request(f"{key} ya está cerrada.")
                solution = clean_text(data.get("solution"), "solución aplicada", 20000)
                closing = clean_text(data.get("closing_comment"), "comentario de cierre", 5000)
                cycle = row["reopen_count"] + 1
                needs_verification = app.settings_cache("verification_required") and user["id"] != row["created_by"]
                cur = c.execute("INSERT INTO resolutions(entity_type, entity_id, cycle, started_at, resolved_at, "
                                "resolved_by, solution, closing_comment, state) VALUES ('incident',?,?,?,?,?,?,?,?)",
                                (iid, cycle, row["cycle_started_at"], ts, user["id"], solution, closing,
                                 "pendiente" if needs_verification else "verificada"))
                mark_first_response(c, row, user)
                if closing:
                    c.execute("INSERT INTO comments(uid, entity_type, entity_id, user_id, body, kind, created_at) "
                              "VALUES (?, 'incident', ?, ?, ?, 'closing', ?)", (f"close-{iid}-{cycle}", iid,
                                                                                 user["id"], closing, ts))
                if needs_verification:
                    c.execute("UPDATE incidents SET status='en_verificacion', verification_requested_at=?, "
                              "verification_requested_by=?, block_reason_code=NULL, block_reason_text=NULL, "
                              "blocked_at=NULL, blocked_by=NULL, updated_at=?, updated_by=?, "
                              "row_version=row_version+1 WHERE id=?", (ts, user["id"], ts, user["id"], iid))
                    log_activity(db, c, user, "incident", iid, key, "verification_requested",
                                 f"Marcó {key} como resuelta: pendiente de verificación",
                                 {"solution": solution[:500], "cycle": cycle}, pid)
                    verifiers = {row["created_by"]} | set(users_with_perm(c, "incidencias.verificar_todas", pid))
                    notify(db, c, verifiers, "verify_request", f"{key} lista para verificar",
                           f"{user['display_name']}: {row['title']}" + (f" — {solution[:140]}" if solution else ""),
                           f"inc-verify-{iid}-{cycle}", "incident", iid, key, exclude=user["id"])
                else:
                    duration = finalize_resolution(c, row, user, cur.lastrowid, ts)
                    log_activity(db, c, user, "incident", iid, key, "resolved", f"Resolvió {key}",
                                 {"from": old, "cycle": cycle, "duration_seconds": duration, "solution": solution},
                                 pid)
                    notify(db, c, [row["created_by"], row["assignee_id"]], "verified", f"{key} resuelta",
                           f"{user['display_name']}: {row['title']}", f"inc-resolved-{iid}-{cycle}", "incident", iid,
                           key, exclude=user["id"])
            elif old in ("resuelta", "duplicada"):
                auth.require(user, "incidencias.reabrir")
                if new_status not in ("pendiente", "en_progreso"):
                    raise bad_request("Una incidencia cerrada solo puede reabrirse como Pendiente o En progreso.")
                if new_status == "en_progreso":
                    auth.require(user, "incidencias.trabajar")
                reason = clean_text(data.get("reopen_reason"), "motivo de la reapertura", 5000)
                last = c.execute("SELECT id FROM resolutions WHERE entity_type='incident' AND entity_id=? "
                                 "AND state='verificada' ORDER BY id DESC LIMIT 1", (iid,)).fetchone()
                if last:
                    c.execute("UPDATE resolutions SET reopened_at=?, reopened_by=?, reopen_reason=? WHERE id=?",
                              (ts, user["id"], reason, last[0]))
                c.execute("UPDATE incidents SET status=?, resolved_at=NULL, resolved_by=NULL, duplicate_of_id=NULL, "
                          "cycle_started_at=?, reopen_count=reopen_count+1, updated_at=?, updated_by=?, "
                          "row_version=row_version+1 WHERE id=?", (new_status, ts, ts, user["id"], iid))
                log_activity(db, c, user, "incident", iid, key, "reopened",
                             f"Reabrió {key}" + (f": {reason}" if reason else ""), {"to": new_status, "reason": reason},
                             pid)
                if reason:
                    c.execute("INSERT INTO comments(uid, entity_type, entity_id, user_id, body, kind, created_at) "
                              "VALUES (?, 'incident', ?, ?, ?, 'reopen', ?)",
                              (f"reopen-{iid}-{row['reopen_count'] + 1}", iid, user["id"], reason, ts))
                notify(db, c, [row["created_by"], row["assignee_id"], row["resolved_by"]], "incident_reopened",
                       f"{key} reabierta", f"{user['display_name']}: {row['title']}" +
                       (f" — {reason[:140]}" if reason else ""), f"inc-reopened-{iid}-{row['reopen_count'] + 1}",
                       "incident", iid, key, exclude=user["id"])
            elif new_status == "bloqueada":
                auth.require(user, "incidencias.trabajar")
                if old == "en_verificacion":
                    raise bad_request(f"{key} está pendiente de verificación.")
                code = clean_choice(data.get("block_reason_code"), "motivo del bloqueo", BLOCK_REASONS)
                text = clean_text(data.get("block_reason_text"), "explicación del bloqueo", 5000,
                                  required=(code == "otro"))
                c.execute("UPDATE incidents SET status='bloqueada', block_reason_code=?, block_reason_text=?, "
                          "blocked_at=?, blocked_by=?, updated_at=?, updated_by=?, row_version=row_version+1 "
                          "WHERE id=?", (code, text, ts, user["id"], ts, user["id"], iid))
                mark_first_response(c, row, user)
                verb = "Actualizó el bloqueo de" if old == "bloqueada" else "Bloqueó"
                log_activity(db, c, user, "incident", iid, key, "blocked",
                             f"{verb} {key}: {BLOCK_REASONS[code]}" + (f" — {text}" if text else ""),
                             {"from": old, "reason_code": code, "reason_text": text}, pid)
                notify(db, c, [row["created_by"]] + users_with_perm(c, "incidencias.verificar_todas", pid),
                       "incident_blocked", f"{key} bloqueada: necesita atención",
                       BLOCK_REASONS[code] + (f" — {text[:160]}" if text else ""), f"inc-blocked-{iid}-{ts}",
                       "incident", iid, key, urgent=True, exclude=user["id"])
            else:
                auth.require(user, "incidencias.trabajar")
                if old == "en_verificacion":
                    raise bad_request(f"{key} está pendiente de verificación: verifícala o recházala.")
                c.execute("UPDATE incidents SET status=?, block_reason_code=NULL, block_reason_text=NULL, "
                          "blocked_at=NULL, blocked_by=NULL, updated_at=?, updated_by=?, row_version=row_version+1 "
                          "WHERE id=?", (new_status, ts, user["id"], iid))
                if new_status == "en_progreso" and not row["assignee_id"]:
                    c.execute("UPDATE incidents SET assignee_id=? WHERE id=?", (user["id"], iid))
                mark_first_response(c, row, user)
                if old == "bloqueada":
                    log_activity(db, c, user, "incident", iid, key, "unblocked", f"Quitó el bloqueo de {key}",
                                 {"previous_reason": row["block_reason_code"]}, pid)
                log_activity(db, c, user, "incident", iid, key, "status",
                             f"Cambió el estado de {key}: {INCIDENT_STATUSES[old]} → {INCIDENT_STATUSES[new_status]}",
                             {"from": old, "to": new_status}, pid)
            db.emit(c, "incident", "incident", iid, actor_id=user["id"])
            new = dict(c.execute("SELECT * FROM incidents WHERE id=?", (iid,)).fetchone())
            return {"item": serialize(new, names(c), projects_map())}

    @router.route("POST", "/api/incidents/{ident}/verify", perm="incidencias.ver", idempotent=True)
    def verify(req, ident):
        """El creador (o quien tenga permiso) acepta o rechaza la resolución."""
        data = req.json
        user = req.user
        accept = bool(data.get("accept"))
        with db.tx() as c:
            row = find_entity(db, user, "incident", ident)
            key, iid, pid = row["key"], row["id"], row["project_id"]
            if row["status"] != "en_verificacion":
                raise bad_request(f"{key} no está pendiente de verificación.")
            if not (row["created_by"] == user["id"] or auth.has_perm(user, "incidencias.verificar_todas")):
                raise forbidden("Solo quien creó la incidencia (o quien tiene permiso de verificar) puede verificarla.")
            res = c.execute("SELECT * FROM resolutions WHERE entity_type='incident' AND entity_id=? AND "
                            "state='pendiente' ORDER BY id DESC LIMIT 1", (iid,)).fetchone()
            ts = now_iso()
            if accept:
                duration = finalize_resolution(c, row, user, res["id"], ts)
                log_activity(db, c, user, "incident", iid, key, "verified",
                             f"Verificó y cerró {key}", {"duration_seconds": duration}, pid)
                notify(db, c, [res["resolved_by"], row["assignee_id"]], "verified", f"{key} verificada y cerrada",
                       f"{user['display_name']} ha confirmado la solución de «{row['title']}»",
                       f"inc-verified-{iid}-{res['id']}", "incident", iid, key, exclude=user["id"])
            else:
                reason = clean_text(data.get("reason"), "motivo del rechazo", 5000, required=True)
                c.execute("UPDATE resolutions SET state='rechazada', verified_by=?, verified_at=?, reject_reason=? "
                          "WHERE id=?", (user["id"], ts, reason, res["id"]))
                c.execute("UPDATE incidents SET status='en_progreso', verification_requested_at=NULL, "
                          "verification_requested_by=NULL, updated_at=?, updated_by=?, row_version=row_version+1 "
                          "WHERE id=?", (ts, user["id"], iid))
                c.execute("INSERT INTO comments(uid, entity_type, entity_id, user_id, body, kind, created_at) "
                          "VALUES (?, 'incident', ?, ?, ?, 'rejection', ?)", (f"reject-{iid}-{res['id']}", iid,
                                                                               user["id"], reason, ts))
                log_activity(db, c, user, "incident", iid, key, "rejected",
                             f"Rechazó la resolución de {key}: {reason}", {"reason": reason}, pid)
                notify(db, c, [res["resolved_by"], row["assignee_id"]], "rejected",
                       f"{key}: resolución rechazada", f"{user['display_name']}: {reason[:200]}",
                       f"inc-rejected-{iid}-{res['id']}", "incident", iid, key, urgent=True, exclude=user["id"])
            db.emit(c, "incident", "incident", iid, actor_id=user["id"])
            new = dict(c.execute("SELECT * FROM incidents WHERE id=?", (iid,)).fetchone())
            return {"item": serialize(new, names(c), projects_map())}

    @router.route("POST", "/api/incidents/{ident}/duplicate", idempotent=True)
    def mark_duplicate(req, ident):
        user = req.user
        auth.require_any(user, "incidencias.trabajar", "incidencias.editar")
        with db.tx() as c:
            row = find_entity(db, user, "incident", ident)
            original = find_entity(db, user, "incident", req.json.get("of") or "")
            if original["id"] == row["id"]:
                raise bad_request("Una incidencia no puede ser duplicada de sí misma.")
            if original["duplicate_of_id"]:
                raise bad_request(f"{original['key']} ya es duplicada de otra; elige la original.")
            if row["status"] in ("resuelta", "duplicada"):
                raise bad_request(f"{row['key']} ya está cerrada.")
            ts = now_iso()
            c.execute("UPDATE incidents SET status='duplicada', duplicate_of_id=?, resolved_at=?, resolved_by=?, "
                      "updated_at=?, updated_by=?, row_version=row_version+1 WHERE id=?",
                      (original["id"], ts, user["id"], ts, user["id"], row["id"]))
            c.execute("INSERT INTO comments(uid, entity_type, entity_id, user_id, body, kind, created_at) "
                      "VALUES (?, 'incident', ?, ?, ?, 'duplicate', ?)",
                      (f"dup-{row['id']}-{original['id']}", row["id"], user["id"],
                       f"Marcada como duplicada de {original['key']}.", ts))
            log_activity(db, c, user, "incident", row["id"], row["key"], "duplicate",
                         f"Marcó {row['key']} como duplicada de {original['key']}", {"of": original["key"]},
                         row["project_id"])
            notify(db, c, [row["created_by"]], "comment", f"{row['key']} es duplicada de {original['key']}",
                   original["title"], f"inc-dup-{row['id']}", "incident", row["id"], row["key"], exclude=user["id"])
            db.emit(c, "incident", "incident", row["id"], actor_id=user["id"])
            db.emit(c, "incident", "incident", original["id"], actor_id=user["id"])
        return {"ok": True}

    @router.route("POST", "/api/incidents/{ident}/seen", perm="incidencias.ver", idempotent=True)
    def mark_seen(req, ident):
        user = req.user
        with db.tx() as c:
            row = find_entity(db, user, "incident", ident)
            if row["created_by"] == user["id"]:
                return {"recorded": False}
            ts = now_iso()
            cur = c.execute("INSERT OR IGNORE INTO incident_views(incident_id, user_id, first_seen_at) VALUES (?,?,?)",
                            (row["id"], user["id"], ts))
            if not cur.rowcount:
                return {"recorded": False}
            log_activity(db, c, user, "incident", row["id"], row["key"], "seen", f"Vio por primera vez {row['key']}",
                         project_id=row["project_id"])
            notify(db, c, [row["created_by"]], "incident_seen", f"{user['display_name']} ha visto {row['key']}",
                   row["title"], f"inc-seen-{row['id']}-{user['id']}", "incident", row["id"], row["key"])
            db.emit(c, "incident", "incident", row["id"], actor_id=user["id"])
            return {"recorded": True, "seen_at": ts}

    @router.route("POST", "/api/incidents/{ident}/solution", idempotent=True)
    def document_solution(req, ident):
        user = req.user
        auth.require_any(user, "incidencias.resolver", "incidencias.editar")
        solution = clean_text(req.json.get("solution"), "solución aplicada", 20000, required=True)
        with db.tx() as c:
            row = find_entity(db, user, "incident", ident)
            last = c.execute("SELECT * FROM resolutions WHERE entity_type='incident' AND entity_id=? AND state IN "
                             "('verificada','pendiente') ORDER BY id DESC LIMIT 1", (row["id"],)).fetchone()
            if not last:
                raise bad_request("La solución se documenta al resolver la incidencia.")
            ts = now_iso()
            if last["solution"]:
                c.execute("INSERT INTO edits(entity_type, entity_id, previous_body, edited_at, edited_by) "
                          "VALUES ('resolution', ?, ?, ?, ?)", (last["id"], last["solution"], ts, user["id"]))
            c.execute("UPDATE resolutions SET solution=? WHERE id=?", (solution, last["id"]))
            c.execute("UPDATE incidents SET updated_at=?, updated_by=?, row_version=row_version+1 WHERE id=?",
                      (ts, user["id"], row["id"]))
            log_activity(db, c, user, "incident", row["id"], row["key"], "solution",
                         f"Documentó la solución de {row['key']}", {"solution": solution[:500]}, row["project_id"])
            db.emit(c, "incident", "incident", row["id"], actor_id=user["id"])
            return {"ok": True}

    app.mark_first_response = mark_first_response
