"""Localización genérica de elementos (incidencias, tareas, decisiones…) por tipo e identificador."""
from __future__ import annotations

from .common import check_project_access
from .web import bad_request, not_found

TABLES = {"incident": "incidents", "task": "tasks", "decision": "decisions", "idea": "ideas", "wiki": "wiki_pages",
          "build": "builds", "version": "versions"}
LABELS = {"incident": "La incidencia", "task": "La tarea", "decision": "La decisión", "idea": "La idea",
          "wiki": "La página", "build": "La build", "version": "La versión"}


def find_entity(db, user, entity_type: str, ident, check_access: bool = True) -> dict:
    table = TABLES.get(entity_type)
    if not table:
        raise bad_request("Tipo de elemento no válido.")
    ident = str(ident).strip()
    row = None
    if ident.isdigit():
        row = db.query_one(f"SELECT * FROM {table} WHERE id=?", (int(ident),))
    elif entity_type in ("incident", "task", "decision"):
        row = db.query_one(f"SELECT * FROM {table} WHERE key=? COLLATE NOCASE", (ident.upper(),))
        if row is None and "-" in ident:
            prefix, _, num = ident.rpartition("-")
            if num.isdigit():
                if entity_type == "incident":
                    row = db.query_one("SELECT i.* FROM incidents i JOIN projects p ON p.id=i.project_id "
                                       "WHERE p.key=? COLLATE NOCASE AND i.number=?", (prefix, int(num)))
                else:
                    row = db.query_one(f"SELECT * FROM {table} WHERE number=?", (int(num),))
    if not row:
        raise not_found(f"No existe {LABELS[entity_type].lower()} {ident}.")
    if check_access and "project_id" in row:
        check_project_access(db, user, row["project_id"])
    return row


def entity_label(row: dict, entity_type: str) -> str:
    return row.get("key") or row.get("title") or row.get("name") or f"#{row['id']}"
