"""Exportación de informes (PDF) y datos estructurados (CSV)."""
from __future__ import annotations

import csv
import io
from datetime import datetime

from .common import BLOCK_REASONS, names, visible_project_ids
from .db import parse_iso
from .web import Response


def local(ts: str | None) -> str:
    if not ts:
        return ""
    try:
        return parse_iso(ts).astimezone().strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return ts


def duration(seconds: int | None) -> str:
    if seconds is None:
        return ""
    d, rem = divmod(int(seconds), 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d} d {h} h"
    if h:
        return f"{h} h {m} min"
    return f"{m} min"


def csv_response(rows: list[list], header: list[str], filename: str) -> Response:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    w.writerow(header)
    for r in rows:
        w.writerow(["" if v is None else v for v in r])
    data = ("﻿" + buf.getvalue()).encode("utf-8")  # BOM para que Excel muestre bien las tildes
    return Response.bytes(data, filename, "text/csv; charset=utf-8")


def register(router, app):
    db = app.db

    def resolutions_for(ids):
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        out = {}
        for r in db.query(f"SELECT * FROM resolutions WHERE entity_type='incident' AND entity_id IN ({marks}) "
                          f"AND state='verificada' ORDER BY id", ids):
            out.setdefault(r["entity_id"], []).append(r)
        return out

    @router.route("GET", "/api/export/incidents.csv", perm="exportar")
    def incidents_csv(req):
        items = app.list_incidents(req, req.user)
        res = resolutions_for([i["id"] for i in items])
        header = ["ID", "Proyecto", "Título", "Descripción", "Prioridad", "Categoría", "Estado", "Responsable", "Creada",
                  "Creada por", "Última modificación", "Modificada por", "Fecha objetivo", "Vencida", "Vista por",
                  "Motivo de bloqueo", "Explicación del bloqueo", "Resuelta", "Resuelta por",
                  "Tiempo hasta resolución", "Solución aplicada", "Comentario de cierre", "Reaperturas",
                  "Pasos para reproducir", "Resultado esperado", "Resultado observado", "Adjuntos", "Comentarios"]
        rows = []
        for i in items:
            last = (res.get(i["id"]) or [None])[-1]
            rows.append([i["key"], i["project_name"], i["title"], i["description"], i["priority_label"],
                         i["category_label"], i["status_label"], i["assignee_name"] or "", local(i["created_at"]),
                         i["created_by_name"], local(i["updated_at"]), i["updated_by_name"], i["target_date"] or "",
                         "Sí" if i["overdue"] else "No",
                         (f"{i['seen_by_name']} ({local(i['seen_at'])})" if i["seen_at"] else "No"), i["block_reason_label"] or "", i["block_reason_text"] or "",
                         local(i["resolved_at"]), i["resolved_by_name"] or "",
                         duration(last["duration_seconds"]) if last and i["status"] == "resuelta" else "",
                         last["solution"] if last else "", last["closing_comment"] if last else "",
                         i["reopen_count"], i["steps"], i["expected"], i["observed"], i["attachments_count"],
                         i["comments_count"]])
        return csv_response(rows, header, f"devcontrol-incidencias-{datetime.now():%Y%m%d-%H%M}.csv")

    @router.route("GET", "/api/export/tasks.csv", perm="exportar")
    def tasks_csv(req):
        items = app.list_tasks(req, req.user)
        header = ["ID", "Proyecto", "Título", "Descripción", "Prioridad", "Estado", "Responsable", "Bloqueo",
                  "Progreso (%)", "Estimación (h)", "Fecha objetivo", "Semana prevista", "Versión", "Etiquetas", "Creada",
                  "Creada por", "Iniciada", "Completada"]
        rows = [[t["key"], t["project_name"], t["title"], t["description"], t["priority_label"], t["status_label"],
                 t["assignee_name"] or "", t["block_reason"] or "", t["progress"], t["estimate_hours"] or "",
                 t["target_date"] or "", t["planned_week"] or "", t["version_name"] or "", t["labels"],
                 local(t["created_at"]), t["created_by_name"], local(t["started_at"]), local(t["completed_at"])]
                for t in items]
        return csv_response(rows, header, f"devcontrol-tareas-{datetime.now():%Y%m%d-%H%M}.csv")

    def activity_rows(req):
        where, params = [], []
        if "actividad.ver_todos" not in req.user["permissions"]:
            where.append("user_id = ?")
            params.append(req.user["id"])
        elif req.q("user_id"):
            where.append("user_id = ?")
            params.append(req.q_int("user_id"))
        if req.q("entity_type"):
            where.append("entity_type = ?")
            params.append(req.q("entity_type"))
        if req.q("from"):
            where.append("created_at >= ?")
            params.append(req.q("from"))
        if req.q("to"):
            where.append("created_at <= ?")
            params.append(req.q("to"))
        if req.q("q"):
            where.append("(summary LIKE ? OR entity_key LIKE ?)")
            params += [f"%{req.q('q')}%"] * 2
        if req.q("project") not in (None, "", "all"):
            where.append("(project_id = ? OR project_id IS NULL)")
            params.append(req.q_int("project"))
        ids = visible_project_ids(db, req.user)
        if ids is not None:
            where.append(f"(project_id IS NULL OR project_id IN ({','.join('?' * len(ids)) or 'NULL'}))")
            params += ids
        sql = "SELECT * FROM activity" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC"
        return sql, params

    app.activity_query = activity_rows

    @router.route("GET", "/api/export/activity.csv", perm="exportar")
    def activity_csv(req):
        sql, params = activity_rows(req)
        user_names = names(db.conn)
        rows = [[local(r["created_at"]), user_names.get(r["user_id"], "Sistema"), r["entity_key"] or "",
                 r["action"], r["summary"]] for r in db.query(sql, params)]
        return csv_response(rows, ["Fecha y hora", "Usuario", "Elemento", "Acción", "Descripción"],
                            f"devcontrol-actividad-{datetime.now():%Y%m%d-%H%M}.csv")

    @router.route("GET", "/api/export/report.pdf", perm="exportar")
    def report_pdf(req):
        from .pdf import PdfDocument
        items = app.list_incidents(req, req.user)
        res = resolutions_for([i["id"] for i in items])
        doc = PdfDocument("DEV CONTROL - Informe de desarrollo")
        doc.text("DEV CONTROL", 20, True, (0.17, 0.24, 0.42), gap=0)
        doc.text("Informe del desarrollo", 13, False, (0.35, 0.35, 0.4), gap=2)
        doc.text(f"Generado el {datetime.now():%d/%m/%Y a las %H:%M} por {req.user['display_name']}", 9,
                 color=(0.4, 0.4, 0.45))
        active = [f"{k}={v}" for k, v in req.query.items() if v and k not in ("access_token",)]
        doc.text("Filtros aplicados: " + (", ".join(active) if active else "ninguno (todas las incidencias)"), 9,
                 color=(0.4, 0.4, 0.45))
        doc.line()
        by_status = {}
        for i in items:
            by_status[i["status_label"]] = by_status.get(i["status_label"], 0) + 1
        doc.text(f"Incidencias en el informe: {len(items)}  -  " +
                 "  ".join(f"{k}: {v}" for k, v in sorted(by_status.items())), 10, True)
        doc.space(6)
        colors = {"critica": (0.75, 0.16, 0.16), "alta": (0.85, 0.45, 0.1), "media": (0.25, 0.4, 0.7),
                  "baja": (0.35, 0.5, 0.4)}
        if not items:
            doc.text("No hay incidencias con estos filtros.", 10)
        for i in items:
            doc.heading_bar(f"{i['key']}  ·  {i['title']}", colors.get(i["priority"], (0.2, 0.2, 0.3)))
            doc.label_value("Estado", i["status_label"] + ("  (VENCIDA)" if i["overdue"] else ""))
            doc.label_value("Prioridad / categoría", f"{i['priority_label']} / {i['category_label']}")
            doc.label_value("Creada", f"{local(i['created_at'])} por {i['created_by_name']}")
            doc.label_value("Última modificación", f"{local(i['updated_at'])} por {i['updated_by_name']}")
            if i["target_date"]:
                doc.label_value("Fecha objetivo", i["target_date"])
            doc.label_value("Proyecto / responsable", f"{i['project_name']} / {i['assignee_name'] or 'sin asignar'}")
            doc.label_value("Vista por", f"{i['seen_by_name']} ({local(i['seen_at'])})" if i["seen_at"] else "Todavía no")
            if i["description"]:
                doc.label_value("Descripción", i["description"])
            if i["steps"]:
                doc.label_value("Pasos para reproducir", i["steps"])
            if i["expected"]:
                doc.label_value("Resultado esperado", i["expected"])
            if i["observed"]:
                doc.label_value("Resultado observado", i["observed"])
            if i["status"] == "bloqueada":
                doc.label_value("BLOQUEO", f"{BLOCK_REASONS.get(i['block_reason_code'], '')}"
                                           f"{' - ' + i['block_reason_text'] if i['block_reason_text'] else ''}")
            for n, r in enumerate(res.get(i["id"], []), 1):
                txt = f"{local(r['resolved_at'])} (tiempo: {duration(r['duration_seconds'])})"
                if r["solution"]:
                    txt += f"\nSolución: {r['solution']}"
                if r["closing_comment"]:
                    txt += f"\nComentario de cierre: {r['closing_comment']}"
                if r["reopened_at"]:
                    txt += f"\nReabierta el {local(r['reopened_at'])}" + \
                           (f": {r['reopen_reason']}" if r["reopen_reason"] else "")
                doc.label_value(f"Resolución {n}", txt)
            if i["attachments_count"] or i["comments_count"]:
                doc.label_value("Adjuntos / comentarios", f"{i['attachments_count']} / {i['comments_count']}")
            doc.space(4)
        if req.q("include_activity", "1") == "1" and items:
            ids = [i["id"] for i in items]
            marks = ",".join("?" * len(ids))
            user_names = names(db.conn)
            acts = db.query(f"SELECT * FROM activity WHERE entity_type='incident' AND entity_id IN ({marks}) "
                            f"ORDER BY id DESC LIMIT 400", ids)
            doc.space(10)
            doc.heading_bar("Registro de actividad de estas incidencias (más reciente primero)")
            for a in acts:
                doc.text(f"{local(a['created_at'])}  ·  {user_names.get(a['user_id'], 'Sistema')}  ·  {a['summary']}",
                         8.5, gap=1)
        return Response.bytes(doc.output(), f"devcontrol-informe-{datetime.now():%Y%m%d-%H%M}.pdf", "application/pdf")
