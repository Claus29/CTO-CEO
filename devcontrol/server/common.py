"""Constantes, validaciones, proyectos visibles, registro de actividad y notificaciones."""
from __future__ import annotations

import contextlib
import json
import logging
import re
import sqlite3
from datetime import date

from .db import Database, now_iso
from .web import bad_request, forbidden, not_found

PRIORITIES = {"critica": "Crítica", "alta": "Alta", "media": "Media", "baja": "Baja"}
CATEGORIES = {"errores": "Errores", "diseno": "Diseño", "funcionalidades": "Funcionalidades",
              "rendimiento": "Rendimiento", "seguridad": "Seguridad"}
INCIDENT_STATUSES = {"pendiente": "Pendiente", "en_progreso": "En progreso", "bloqueada": "Bloqueada",
                     "en_verificacion": "En verificación", "resuelta": "Resuelta", "duplicada": "Duplicada"}
OPEN_INCIDENT = ("pendiente", "en_progreso", "bloqueada", "en_verificacion")
TASK_STATUSES = {"pendiente": "Pendiente", "en_progreso": "En progreso", "bloqueada": "Bloqueada",
                 "en_verificacion": "En verificación", "resuelto": "Resuelto"}
IDEA_STATUSES = {"propuesta": "Propuesta", "planificada": "Planificada", "en_desarrollo": "En desarrollo",
                 "descartada": "Descartada"}
VERSION_STATUSES = {"planificada": "Planificada", "en_curso": "En curso", "publicada": "Publicada"}
BLOCK_REASONS = {
    "falta_informacion": "Falta de información", "falta_acceso": "Falta de acceso o permisos",
    "dependencia": "Dependencia de otro trabajo", "problema_tecnico": "Problema técnico o de código",
    "falta_decision": "Falta de una decisión", "otro": "Otro motivo",
}
FREQUENCIES = {"siempre": "Siempre", "a_veces": "A veces", "una_vez": "Una vez"}
ENVIRONMENTS = {"produccion": "Producción", "beta": "Beta", "interno": "Interno / desarrollo"}
PLATFORMS = {"ios": "iOS", "android": "Android", "web": "Web", "windows": "Windows", "otra": "Otra"}
BUILD_CHANNELS = {"interno": "Interno", "beta": "Beta", "produccion": "Producción"}
STORES = {"app_store": "App Store", "google_play": "Google Play", "otra": "Otra tienda"}
STORE_STATUSES = {"preparando": "Preparando", "enviada": "Enviada", "en_revision": "En revisión",
                  "rechazada": "Rechazada", "aprobada": "Aprobada", "publicada": "Publicada", "retirada": "Retirada"}
TEST_RESULTS = {"pendiente": "Pendiente", "ok": "Correcto", "fallo": "Fallo", "bloqueado": "Bloqueado",
                "omitido": "Omitido"}
DECISION_STATUSES = {"propuesta": "Propuesta", "aceptada": "Aceptada", "rechazada": "Rechazada",
                     "sustituida": "Sustituida"}
REPORT_FIELDS = {"steps": "Pasos para reproducir", "expected": "Resultado esperado",
                 "observed": "Resultado observado", "device": "Dispositivo", "os_version": "Sistema y versión",
                 "app_version": "Versión de la app", "environment": "Entorno", "frequency": "Frecuencia"}

NOTIFICATION_TYPES = {
    "incident_created": "Nueva incidencia", "incident_critical": "Incidencia crítica",
    "assigned": "Algo asignado a ti", "mention": "Te han mencionado",
    "verify_request": "Pendiente de que lo verifiques", "verified": "Tu trabajo ha sido verificado",
    "rejected": "Tu trabajo ha sido rechazado en la verificación",
    "incident_reopened": "Incidencia reabierta", "incident_seen": "Han visto tu incidencia",
    "incident_blocked": "Bloqueos", "comment": "Nuevos comentarios", "message_new": "Mensajes del chat",
    "dependency_unblocked": "Ya puedes empezar algo que estaba bloqueado", "sla": "Plazos (SLA) en riesgo",
    "due_soon": "Fechas objetivo próximas o vencidas", "incident_stale": "Incidencias estancadas",
    "renewal": "Renovaciones a punto de caducar", "store": "Publicaciones en tiendas",
    "standup": "Daily y bloqueos declarados", "reminder": "Recordatorios",
    "call_incoming": "Llamadas entrantes", "call_missed": "Llamadas perdidas",
    "daily_summary": "Resumen diario", "weekly_summary": "Resumen semanal", "system": "Sistema y seguridad",
    # 2.2
    "task_created": "Tareas nuevas en tus proyectos", "task_updated": "Cambios en tareas tuyas o que sigues",
    "incident_updated": "Cambios en incidencias tuyas o que sigues",
    "status_changed": "Cambios de estado (en curso, en verificación, resuelta, reabierta…)",
    "chat_reply": "Respuestas a tus mensajes", "pending_digest": "Tus pendientes (cada mañana)",
    "verify_waiting": "Verificaciones que llevan más de un día esperando",
    "idea": "Ideas nuevas y cambios en tus ideas", "release": "Versiones y objetivos del equipo",
    "quality": "Builds nuevas y pruebas", "wiki": "Wiki y decisiones", "team_absence": "Ausencias del equipo",
    "calendar": "Fechas del calendario", "account": "Tu cuenta y tus proyectos",
    "server_health": "Estado del servidor (copias y espacio en disco)",
}

# Agrupación para la pantalla de preferencias.
NOTIFICATION_AREAS = {
    "Tareas e incidencias": ["task_created", "task_updated", "status_changed", "assigned", "verify_request",
                             "verified", "rejected", "verify_waiting", "incident_created", "incident_critical",
                             "incident_updated", "incident_reopened", "incident_seen", "incident_blocked",
                             "dependency_unblocked", "comment", "mention"],
    "Pendientes y plazos": ["pending_digest", "due_soon", "sla", "incident_stale", "reminder"],
    "Chat y llamadas": ["message_new", "chat_reply", "call_incoming", "call_missed"],
    "Planificación": ["idea", "release", "calendar", "renewal"],
    "Calidad": ["quality", "store"],
    "Equipo y conocimiento": ["standup", "team_absence", "wiki", "daily_summary", "weekly_summary"],
    "Cuenta y sistema": ["account", "system", "server_health"],
}
# «Lo importante»: lo que te afecta directamente. «Solo lo mío»: lo que te toca hacer a ti.
NOTIFICATION_PRESETS = {
    "importante": {"assigned", "mention", "verify_request", "verified", "rejected", "verify_waiting",
                   "incident_critical", "incident_reopened", "task_updated", "incident_updated", "status_changed",
                   "comment", "chat_reply", "call_incoming", "call_missed", "pending_digest", "due_soon", "sla",
                   "dependency_unblocked", "reminder", "account", "system", "server_health", "release",
                   "incident_blocked", "message_new", "calendar"},
    "solo_mio": {"assigned", "mention", "verify_request", "verified", "rejected", "verify_waiting",
                 "incident_reopened", "chat_reply", "call_incoming", "call_missed", "pending_digest", "due_soon",
                 "sla", "dependency_unblocked", "reminder", "account", "system"},
}

# Descripciones de tareas, incidencias e ideas: hasta 500 000 caracteres (antes 20 000).
LONG_TEXT_MAX = 500_000
LIST_PREVIEW_CHARS = 1_000   # en los listados solo viaja el principio (la ficha la trae completa)

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
MENTION_RE = re.compile(r"(?<![\w.])@([A-Za-z0-9_.-]{2,40})")


# -- validaciones -------------------------------------------------------------------
def clean_text(value, field: str, max_len: int, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise bad_request(f"El campo «{field}» debe ser texto.")
    value = value.replace("\r\n", "\n").strip()
    if required and not value:
        raise bad_request(f"El campo «{field}» es obligatorio.", "campo_obligatorio")
    if len(value) > max_len:
        raise bad_request(f"El campo «{field}» es demasiado largo (máximo {max_len} caracteres).")
    return value


def clean_choice(value, field: str, choices: dict, default: str | None = None) -> str:
    if value in (None, "") and default is not None:
        return default
    if value not in choices:
        raise bad_request(f"Valor no válido para «{field}»: {value!r}.")
    return value


def clean_date(value, field: str) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not DATE_RE.match(value):
        raise bad_request(f"La fecha «{field}» debe tener el formato AAAA-MM-DD.")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise bad_request(f"La fecha «{field}» no existe.") from None
    return value


def clean_uid(value) -> str:
    if not isinstance(value, str) or not UID_RE.match(value):
        raise bad_request("Falta el identificador único de la operación (uid).")
    return value


def clean_int(value, field: str, lo: int | None = None, hi: int | None = None, optional: bool = False):
    if value in (None, "") and optional:
        return None
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise bad_request(f"El campo «{field}» debe ser un número entero.") from None
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise bad_request(f"El campo «{field}» está fuera de rango.")
    return v


def clean_float(value, field: str, lo: float = 0, hi: float = 10000, optional: bool = True):
    if value in (None, "") and optional:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise bad_request(f"El campo «{field}» debe ser un número.") from None
    if v < lo or v > hi:
        raise bad_request(f"El campo «{field}» está fuera de rango.")
    return round(v, 2)


def clean_labels(value) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, list):
        items = value
    elif isinstance(value, str):
        items = value.split(",")
    else:
        raise bad_request("Etiquetas no válidas.")
    out = []
    for it in items:
        it = str(it).strip().lower()[:30]
        if it and it not in out:
            out.append(it)
    return ",".join(out[:12])


def check_version(row: dict, data: dict, label: str, required: bool = True) -> None:
    from .web import HttpError
    version = data.get("version")
    if version is None:
        if required:
            raise bad_request("Falta la versión del elemento (control de cambios simultáneos).")
        return
    if int(version) != row["row_version"]:
        raise HttpError(409, "conflicto", f"{label} ha sido modificado por otra persona mientras lo editabas. Se han "
                                          "cargado los datos actuales; revisa y vuelve a aplicar tu cambio.",
                        {"current_version": row["row_version"]})


# -- proyectos visibles -----------------------------------------------------------------
def visible_project_ids(db: Database, user: dict) -> list[int] | None:
    """None = todos. Si no, los proyectos de los que el usuario es miembro."""
    if "proyectos.ver_todos" in user.get("permissions", ()):
        return None
    return [r["project_id"] for r in db.query("SELECT project_id FROM project_members WHERE user_id=?",
                                              (user["id"],))]


def project_clause(db: Database, user: dict, column: str = "project_id", req=None) -> tuple[str, list]:
    """Condición SQL que limita al proyecto elegido (?project=) y a los visibles."""
    clauses, params = [], []
    ids = visible_project_ids(db, user)
    if ids is not None:
        if not ids:
            return "1=0", []
        clauses.append(f"{column} IN ({','.join('?' * len(ids))})")
        params += ids
    if req is not None and req.q("project") not in (None, "", "all"):
        try:
            pid = int(req.q("project"))
        except ValueError:
            raise bad_request("Proyecto no válido.") from None
        clauses.append(f"{column} = ?")
        params.append(pid)
    return (" AND ".join(clauses) or "1=1"), params


def require_project(db: Database, user: dict, project_id) -> dict:
    if project_id in (None, ""):
        raise bad_request("Indica el proyecto.")
    try:
        project_id = int(project_id)
    except (TypeError, ValueError):
        raise bad_request("Proyecto no válido.") from None
    row = db.query_one("SELECT * FROM projects WHERE id=?", (project_id,))
    if not row:
        raise not_found("El proyecto no existe.")
    ids = visible_project_ids(db, user)
    if ids is not None and project_id not in ids:
        raise forbidden("No eres miembro de este proyecto.")
    return row


def check_project_access(db: Database, user: dict, project_id: int | None) -> None:
    if project_id is None:
        return
    ids = visible_project_ids(db, user)
    if ids is not None and project_id not in ids:
        raise not_found("No se ha encontrado el elemento solicitado.")


def default_project_id(db: Database, user: dict) -> int:
    ids = visible_project_ids(db, user)
    pid = db.meta("default_project")
    if pid and (ids is None or int(pid) in ids):
        return int(pid)
    row = db.query_one("SELECT id FROM projects WHERE archived=0 ORDER BY id LIMIT 1") if ids is None else \
        (db.query_one(f"SELECT id FROM projects WHERE id IN ({','.join('?' * len(ids))}) ORDER BY id LIMIT 1", ids)
         if ids else None)
    if not row:
        raise bad_request("No tienes ningún proyecto. Pide a un administrador que te añada a uno.")
    return row["id"]


# -- usuarios -----------------------------------------------------------------------------
def users_with_perm(conn: sqlite3.Connection, perm: str, project_id: int | None = None) -> list[int]:
    rows = conn.execute("SELECT u.id FROM users u JOIN role_permissions rp ON rp.role_id = u.role_id "
                        "WHERE rp.perm = ? AND u.active = 1", (perm,)).fetchall()
    ids = [r[0] for r in rows]
    if project_id is not None:
        members = {r[0] for r in conn.execute("SELECT user_id FROM project_members WHERE project_id=?",
                                              (project_id,))}
        seers = {r[0] for r in conn.execute("SELECT u.id FROM users u JOIN role_permissions rp ON rp.role_id=u.role_id "
                                            "WHERE rp.perm='proyectos.ver_todos' AND u.active=1")}
        ids = [i for i in ids if i in members or i in seers]
    return ids


def other_users(conn: sqlite3.Connection, actor_id: int) -> list[int]:
    return [r[0] for r in conn.execute("SELECT id FROM users WHERE id<>? AND active=1", (actor_id,))]


def all_users(conn: sqlite3.Connection) -> list[int]:
    return [r[0] for r in conn.execute("SELECT id FROM users WHERE active=1")]


def names(conn: sqlite3.Connection) -> dict[int, str]:
    return {r[0]: r[1] for r in conn.execute("SELECT id, display_name FROM users")}


def mentioned_users(conn: sqlite3.Connection, text: str) -> list[int]:
    found = {m.lower() for m in MENTION_RE.findall(text or "")}
    if not found:
        return []
    rows = conn.execute("SELECT id, username FROM users WHERE active=1").fetchall()
    return [r[0] for r in rows if r[1].lower() in found]


def user_exists(conn, user_id, field: str = "responsable") -> int | None:
    if user_id in (None, "", 0):
        return None
    uid = clean_int(user_id, field)
    if not conn.execute("SELECT 1 FROM users WHERE id=? AND active=1", (uid,)).fetchone():
        raise bad_request(f"El usuario indicado como {field} no existe o está desactivado.")
    return uid


# -- registro de actividad y avisos ------------------------------------------------------------
def log_activity(db: Database, conn: sqlite3.Connection, user: dict | None, entity_type: str,
                 entity_id: int | None, entity_key: str | None, action: str, summary: str,
                 details: dict | None = None, project_id: int | None = None) -> None:
    ts = now_iso()
    conn.execute(
        "INSERT INTO activity(user_id, project_id, entity_type, entity_id, entity_key, action, summary, details, "
        "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (user["id"] if user else None, project_id, entity_type, entity_id, entity_key, action, summary,
         json.dumps(details, ensure_ascii=False) if details else None, ts))
    if user:
        conn.execute("UPDATE users SET last_action_at=?, last_action=? WHERE id=?", (ts, summary[:200], user["id"]))
    db.emit(conn, "activity", entity_type, entity_id, actor_id=user["id"] if user else None)
    for hook in getattr(db, "activity_hooks", ()):
        try:   # los avisos nunca deben impedir que se guarde el cambio
            with savepoint(conn):
                hook(conn, user, entity_type, entity_id, entity_key, action, summary, details, project_id)
        except Exception:
            logging.getLogger("devcontrol.avisos").exception("Error al generar avisos de %s %s", entity_type, action)


@contextlib.contextmanager
def savepoint(conn: sqlite3.Connection):
    conn.execute("SAVEPOINT avisos")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK TO avisos")
        conn.execute("RELEASE avisos")
        raise
    conn.execute("RELEASE avisos")


def notify(db: Database, conn: sqlite3.Connection, user_ids, ntype: str, title: str, body: str,
           dedupe_key: str, entity_type: str | None = None, entity_id: int | None = None,
           entity_key: str | None = None, urgent: bool = False, exclude: int | None = None) -> int:
    """Un aviso por destinatario; la clave de deduplicación impide avisos repetidos."""
    created = 0
    for uid in set(user_ids):
        if uid is None or uid == exclude:
            continue
        pref = conn.execute("SELECT in_app FROM notification_prefs WHERE user_id=? AND type=?",
                            (uid, ntype)).fetchone()
        if pref is not None and not pref[0]:
            continue
        cur = conn.execute(
            "INSERT OR IGNORE INTO notifications(user_id, type, title, body, entity_type, entity_id, entity_key, "
            "urgent, dedupe_key, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (uid, ntype, title, body[:500], entity_type, entity_id, entity_key, 1 if urgent else 0, dedupe_key,
             now_iso()))
        if cur.rowcount:
            created += 1
            db.emit(conn, "notification", "notification", cur.lastrowid, user_id=uid)
            if entity_type and entity_id:
                getattr(db, "tx_notified", set()).add((uid, entity_type, entity_id))
    return created
