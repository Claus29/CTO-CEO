"""Migraciones numeradas del esquema.

- Base de datos nueva: se crea el esquema actual completo.
- Base de datos de la versión 1.x (esquema 1): se convierte a la 2.0 dentro de
  una única transacción. Antes se llama a `before_migration` (copia de seguridad).
- Versiones futuras: añadir funciones `_to_N` y registrarlas en MIGRATIONS.
"""
from __future__ import annotations

import re

from .db import SCHEMA, SCHEMA_VERSION, now_iso
from .permissions import seed_roles

DEFAULT_PROJECT_KEY = "INC"


def _statements(script: str) -> list[str]:
    return [s.strip() for s in script.split(";\n") if "CREATE" in s]


def _tables(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}


def _current_version(conn) -> int:
    tables = _tables(conn)
    if "meta" not in tables:
        return 0
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return int(row[0]) if row else 0


def _create_schema(conn) -> None:
    for stmt in _statements(SCHEMA):
        conn.execute(stmt)


def migrate(db) -> None:
    conn = db.conn
    version = _current_version(conn)
    if version == SCHEMA_VERSION:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _create_schema(conn)  # añade tablas nuevas si las hubiera
            seed_roles(conn, now_iso())
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        return
    if version > SCHEMA_VERSION:
        raise RuntimeError(f"La base de datos es de una versión más nueva del programa (esquema {version}). "
                           "Actualiza DEV CONTROL antes de abrirla.")
    if version and db.before_migration:
        db.before_migration(db, version)
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("BEGIN IMMEDIATE")
    try:
        if version == 0:
            _create_schema(conn)
            seed_roles(conn, now_iso())
        elif version == 1:
            _from_v1(conn)
        conn.execute("INSERT INTO meta(key, value) VALUES ('schema_version', ?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(SCHEMA_VERSION),))
        problems = conn.execute("PRAGMA foreign_key_check").fetchall()
        if problems:
            raise RuntimeError(f"La migración dejaría referencias rotas ({len(problems)}).")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def _cols(conn, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _from_v1(conn) -> None:
    """Convierte una base de datos de la versión 1.x a la 2.0 sin perder nada."""
    now = now_iso()
    renamed = ["users", "sessions", "incidents", "incident_resolutions", "comments", "messages", "chat_state",
               "versions", "tasks", "ideas", "project_dates", "activity", "notifications", "attachments",
               "message_reads", "edits", "goals", "notification_prefs", "changes", "idempotency", "counters",
               "settings"]
    existing = _tables(conn)
    for t in renamed:
        if t in existing:
            conn.execute(f"ALTER TABLE {t} RENAME TO {t}_v1")
    _create_schema(conn)
    roles = seed_roles(conn, now)

    # Prefijo antiguo de los identificadores (p. ej. «ABC-001»): se detecta, no se supone.
    old_prefix = None
    row = conn.execute("SELECT key FROM incidents_v1 LIMIT 1").fetchone() if "incidents" in existing else None
    if row and re.match(r"^[A-Za-z]+-\d+$", row[0]):
        old_prefix = row[0].rsplit("-", 1)[0]

    # Configuración, contadores y estructuras sin cambios de forma
    if "settings" in existing:
        conn.execute("INSERT OR REPLACE INTO settings(key, value) SELECT key, value FROM settings_v1")
    conn.execute("INSERT INTO counters(name, value) SELECT name, value FROM counters_v1 WHERE name <> 'incident'")

    # Usuarios: owner -> administrador, cto -> responsable técnico
    conn.execute(
        "INSERT INTO users(id, username, display_name, role_id, password_hash, active, created_at, mute_until, "
        "last_seen_at, last_action_at, last_action) SELECT id, username, display_name, "
        "CASE role WHEN 'owner' THEN ? ELSE ? END, password_hash, active, created_at, mute_until, last_seen_at, "
        "last_action_at, last_action FROM users_v1", (roles["admin"], roles["cto"]))
    conn.execute("INSERT INTO sessions(token_hash, user_id, created_at, expires_at, remember, client_ip, "
                 "last_used_at) SELECT token_hash, user_id, created_at, expires_at, remember, client_ip, "
                 "last_used_at FROM sessions_v1")

    # Proyecto por defecto con todos los miembros
    owner = conn.execute("SELECT MIN(id) FROM users").fetchone()[0]
    cur = conn.execute("INSERT INTO projects(uid, key, name, description, created_by, created_at, updated_at) "
                       "VALUES (?,?,?,?,?,?,?)", (_uid(), DEFAULT_PROJECT_KEY, "General",
                                                  "Proyecto creado al migrar desde la versión 1", owner, now, now))
    pid = cur.lastrowid
    conn.execute("INSERT INTO project_members(project_id, user_id) SELECT ?, id FROM users", (pid,))
    gen = conn.execute("INSERT INTO channels(uid, name, kind, created_at) VALUES (?, 'General', 'general', ?)",
                       (_uid(), now)).lastrowid

    def new_key(number: int) -> str:
        return f"{DEFAULT_PROJECT_KEY}-{number:03d}"

    # Incidencias
    max_number = 0
    for r in conn.execute("SELECT * FROM incidents_v1 ORDER BY id").fetchall():
        r = dict(r)
        max_number = max(max_number, r["number"])
        conn.execute(
            "INSERT INTO incidents(id, uid, project_id, number, key, title, description, priority, category, steps, "
            "expected, observed, target_date, status, block_reason_code, block_reason_text, blocked_at, blocked_by, "
            "created_by, created_at, updated_at, updated_by, cycle_started_at, first_response_at, resolved_at, "
            "resolved_by, reopen_count, row_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (r["id"], r["uid"], pid, r["number"], new_key(r["number"]), r["title"], r["description"], r["priority"],
             r["category"], r["steps"], r["expected"], r["observed"], r["target_date"], r["status"],
             r["block_reason_code"], r["block_reason_text"], r["blocked_at"], r.get("blocked_by"), r["created_by"],
             r["created_at"], r["updated_at"], r["updated_by"], r["cycle_started_at"], r.get("seen_by_cto_at"),
             r["resolved_at"], r["resolved_by"], r["reopen_count"], r["row_version"]))
        if r.get("seen_by_cto_id"):
            conn.execute("INSERT OR IGNORE INTO incident_views(incident_id, user_id, first_seen_at) VALUES (?,?,?)",
                         (r["id"], r["seen_by_cto_id"], r["seen_by_cto_at"]))
    old_counter = conn.execute("SELECT value FROM counters_v1 WHERE name='incident'").fetchone()
    conn.execute("INSERT OR REPLACE INTO counters(name, value) VALUES (?, ?)",
                 (f"incident:{pid}", max(max_number, old_counter[0] if old_counter else 0)))

    # Resoluciones (ya verificadas en la 1.x)
    conn.execute(
        "INSERT INTO resolutions(entity_type, entity_id, cycle, started_at, resolved_at, resolved_by, solution, "
        "closing_comment, state, verified_by, verified_at, duration_seconds, reopened_at, reopened_by, reopen_reason) "
        "SELECT 'incident', incident_id, cycle, started_at, resolved_at, resolved_by, solution, closing_comment, "
        "'verificada', resolved_by, resolved_at, duration_seconds, reopened_at, reopened_by, reopen_reason "
        "FROM incident_resolutions_v1")
    conn.execute("INSERT INTO comments(id, uid, entity_type, entity_id, user_id, body, kind, created_at, edited_at) "
                 "SELECT id, uid, 'incident', incident_id, user_id, body, kind, created_at, edited_at FROM comments_v1")
    conn.execute("INSERT INTO edits SELECT * FROM edits_v1")
    conn.execute("INSERT INTO attachments SELECT * FROM attachments_v1")

    # Chat
    conn.execute("INSERT INTO messages(id, uid, channel_id, user_id, body, reply_to_id, incident_id, created_at, "
                 "edited_at) SELECT id, uid, ?, user_id, body, reply_to_id, incident_id, created_at, edited_at "
                 "FROM messages_v1", (gen,))
    conn.execute("INSERT INTO message_reads SELECT * FROM message_reads_v1")
    conn.execute("INSERT INTO chat_state(user_id, channel_id, last_read_id) SELECT user_id, ?, last_read_id "
                 "FROM chat_state_v1", (gen,))

    # Planificación
    conn.execute("INSERT INTO versions(id, uid, project_id, name, description, target_date, status, created_by, "
                 "created_at, updated_at, row_version) SELECT id, uid, ?, name, description, target_date, status, "
                 "created_by, created_at, updated_at, row_version FROM versions_v1", (pid,))
    conn.execute(
        "INSERT INTO tasks(id, uid, project_id, number, key, title, description, priority, status, block_reason, "
        "progress, target_date, planned_week, version_id, idea_id, created_by, created_at, updated_at, updated_by, "
        "started_at, completed_at, row_version) SELECT id, uid, ?, number, key, title, description, priority, status, "
        "block_reason, progress, target_date, planned_week, version_id, idea_id, created_by, created_at, updated_at, "
        "updated_by, started_at, completed_at, row_version FROM tasks_v1", (pid,))
    conn.execute("INSERT INTO ideas(id, uid, project_id, title, description, priority, version_id, status, task_id, "
                 "created_by, created_at, updated_at, updated_by, row_version) SELECT id, uid, ?, title, description, "
                 "priority, version_id, status, task_id, created_by, created_at, updated_at, updated_by, row_version "
                 "FROM ideas_v1", (pid,))
    conn.execute("INSERT INTO goals SELECT * FROM goals_v1")
    conn.execute("INSERT INTO project_dates(id, uid, project_id, title, date, description, created_by, created_at, "
                 "updated_at) SELECT id, uid, ?, title, date, description, created_by, created_at, updated_at "
                 "FROM project_dates_v1", (pid,))

    # Registro, avisos y preferencias
    conn.execute("INSERT INTO activity(id, user_id, project_id, entity_type, entity_id, entity_key, action, summary, "
                 "details, created_at) SELECT id, user_id, ?, entity_type, entity_id, entity_key, action, summary, "
                 "details, created_at FROM activity_v1", (pid,))
    conn.execute("INSERT INTO notifications SELECT * FROM notifications_v1")
    conn.execute("INSERT INTO notification_prefs SELECT * FROM notification_prefs_v1")
    conn.execute("INSERT INTO idempotency SELECT * FROM idempotency_v1")

    # Los textos que citaban los ID antiguos pasan al prefijo nuevo
    if old_prefix and old_prefix.upper() != DEFAULT_PROJECT_KEY:
        pattern = re.compile(rf"\b{re.escape(old_prefix)}-(\d+)\b", re.IGNORECASE)

        def conv(text):
            return pattern.sub(lambda m: new_key(int(m.group(1))), text) if text else text
        for table, cols in (("messages", ("body",)), ("comments", ("body",)), ("activity", ("summary", "entity_key")),
                            ("notifications", ("title", "body", "entity_key")),
                            ("resolutions", ("solution", "closing_comment", "reopen_reason"))):
            for r in conn.execute(f"SELECT id, {', '.join(cols)} FROM {table}").fetchall():
                new = [conv(r[c]) for c in cols]
                if new != [r[c] for c in cols]:
                    conn.execute(f"UPDATE {table} SET {', '.join(c + '=?' for c in cols)} WHERE id=?", (*new, r["id"]))
        conn.execute("INSERT INTO activity(project_id, entity_type, action, summary, created_at) "
                     "VALUES (?, 'system', 'migration', ?, ?)",
                     (pid, f"Migración desde la versión 1: los identificadores {old_prefix}-NNN pasan a "
                           f"{DEFAULT_PROJECT_KEY}-NNN", now))
    conn.execute("INSERT INTO activity(project_id, entity_type, action, summary, created_at) "
                 "VALUES (?, 'system', 'migration', 'Base de datos actualizada a la versión 2', ?)", (pid, now))

    for t in renamed:
        conn.execute(f"DROP TABLE IF EXISTS {t}_v1")
    conn.execute("INSERT INTO meta(key, value) VALUES ('default_project', ?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(pid),))
    rebuild_search(conn)


def rebuild_search(conn) -> None:
    conn.execute("DELETE FROM search_index")
    conn.execute("INSERT INTO search_index(kind, ref_id, project_id, key, title, body) "
                 "SELECT 'incident', id, project_id, key, title, description || ' ' || steps || ' ' || observed "
                 "FROM incidents")
    conn.execute("INSERT INTO search_index(kind, ref_id, project_id, key, title, body) "
                 "SELECT 'task', id, project_id, key, title, description FROM tasks")
    conn.execute("INSERT INTO search_index(kind, ref_id, project_id, key, title, body) "
                 "SELECT 'message', m.id, c.project_id, '', '', m.body FROM messages m JOIN channels c "
                 "ON c.id = m.channel_id")
    conn.execute("INSERT INTO search_index(kind, ref_id, project_id, key, title, body) "
                 "SELECT 'idea', id, project_id, '', title, description FROM ideas")
    conn.execute("INSERT INTO search_index(kind, ref_id, project_id, key, title, body) "
                 "SELECT 'wiki', id, project_id, '', title, body FROM wiki_pages")
    conn.execute("INSERT INTO search_index(kind, ref_id, project_id, key, title, body) "
                 "SELECT 'decision', id, project_id, key, title, context || ' ' || decision FROM decisions")


def _uid() -> str:
    import secrets
    return secrets.token_hex(16)


__all__ = ["migrate", "rebuild_search", "DEFAULT_PROJECT_KEY"]
