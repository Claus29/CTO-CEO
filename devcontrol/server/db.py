"""Almacenamiento central: SQLite (WAL) con escrituras serializadas y migraciones numeradas.

Todas las operaciones de escritura pasan por un único bloqueo: para un equipo
pequeño es lo más fiable (no hay escrituras que se pisen, los identificadores
nunca se repiten y un corte de luz no deja nada a medias).
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS counters (name TEXT PRIMARY KEY, value INTEGER NOT NULL);

-- Usuarios, roles y permisos ---------------------------------------------------
CREATE TABLE IF NOT EXISTS roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    locked INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS role_permissions (
    role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    perm TEXT NOT NULL,
    PRIMARY KEY (role_id, perm)
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name TEXT NOT NULL,
    email TEXT NOT NULL DEFAULT '',
    role_id INTEGER NOT NULL REFERENCES roles(id),
    password_hash TEXT NOT NULL,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    totp_secret TEXT,
    totp_enabled INTEGER NOT NULL DEFAULT 0,
    recovery_codes TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    capacity_hours REAL NOT NULL DEFAULT 40,
    created_at TEXT NOT NULL,
    mute_until TEXT,
    last_seen_at TEXT,
    last_login_at TEXT,
    last_action_at TEXT,
    last_action TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    remember INTEGER NOT NULL DEFAULT 0,
    client_ip TEXT,
    client_label TEXT,
    last_used_at TEXT
);

-- Proyectos -------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    key TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL DEFAULT '#5b8def',
    archived INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    row_version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS project_members (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    PRIMARY KEY (project_id, user_id)
);

-- Incidencias ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    number INTEGER NOT NULL,
    key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    priority TEXT NOT NULL,
    category TEXT NOT NULL,
    steps TEXT NOT NULL DEFAULT '',
    expected TEXT NOT NULL DEFAULT '',
    observed TEXT NOT NULL DEFAULT '',
    device TEXT NOT NULL DEFAULT '',
    os_version TEXT NOT NULL DEFAULT '',
    app_version TEXT NOT NULL DEFAULT '',
    environment TEXT NOT NULL DEFAULT '',
    frequency TEXT NOT NULL DEFAULT '',
    na_fields TEXT NOT NULL DEFAULT '{}',
    labels TEXT NOT NULL DEFAULT '',
    target_date TEXT,
    status TEXT NOT NULL DEFAULT 'pendiente',
    assignee_id INTEGER REFERENCES users(id),
    estimate_hours REAL,
    found_build_id INTEGER,
    fixed_build_id INTEGER,
    duplicate_of_id INTEGER REFERENCES incidents(id),
    block_reason_code TEXT,
    block_reason_text TEXT,
    blocked_at TEXT,
    blocked_by INTEGER,
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by INTEGER NOT NULL REFERENCES users(id),
    cycle_started_at TEXT NOT NULL,
    first_response_at TEXT,
    verification_requested_at TEXT,
    verification_requested_by INTEGER,
    resolved_at TEXT,
    resolved_by INTEGER,
    reopen_count INTEGER NOT NULL DEFAULT 0,
    row_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE (project_id, number)
);
CREATE INDEX IF NOT EXISTS ix_incidents_status ON incidents(status);
CREATE INDEX IF NOT EXISTS ix_incidents_project ON incidents(project_id);
CREATE INDEX IF NOT EXISTS ix_incidents_assignee ON incidents(assignee_id);

CREATE TABLE IF NOT EXISTS incident_views (
    incident_id INTEGER NOT NULL REFERENCES incidents(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    first_seen_at TEXT NOT NULL,
    PRIMARY KEY (incident_id, user_id)
);

-- Resoluciones (incidencias y tareas), con su verificación
CREATE TABLE IF NOT EXISTS resolutions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    cycle INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    resolved_at TEXT NOT NULL,
    resolved_by INTEGER NOT NULL REFERENCES users(id),
    solution TEXT NOT NULL DEFAULT '',
    closing_comment TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'verificada',
    verified_by INTEGER,
    verified_at TEXT,
    reject_reason TEXT,
    duration_seconds INTEGER,
    reopened_at TEXT,
    reopened_by INTEGER,
    reopen_reason TEXT
);
CREATE INDEX IF NOT EXISTS ix_resolutions_entity ON resolutions(entity_type, entity_id);

-- Comentarios genéricos (incidencias, tareas, decisiones…)
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(id),
    body TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'comment',
    created_at TEXT NOT NULL,
    edited_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_comments_entity ON comments(entity_type, entity_id);

CREATE TABLE IF NOT EXISTS edits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    previous_body TEXT NOT NULL,
    edited_at TEXT NOT NULL,
    edited_by INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT UNIQUE,
    owner_type TEXT NOT NULL,
    owner_id INTEGER NOT NULL,
    filename TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    size INTEGER NOT NULL,
    content_type TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    uploaded_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_attachments_owner ON attachments(owner_type, owner_id);

-- Relaciones: dependencias ("bloqueada por") y enlaces genéricos
CREATE TABLE IF NOT EXISTS dependencies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    blocker_type TEXT NOT NULL,
    blocker_id INTEGER NOT NULL,
    blocked_type TEXT NOT NULL,
    blocked_id INTEGER NOT NULL,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (blocker_type, blocker_id, blocked_type, blocked_id)
);
CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_type TEXT NOT NULL,
    from_id INTEGER NOT NULL,
    to_type TEXT NOT NULL,
    to_id INTEGER NOT NULL,
    kind TEXT NOT NULL DEFAULT 'relacionado',
    created_by INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE (from_type, from_id, to_type, to_id, kind)
);

-- Chat ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'personalizado',
    project_id INTEGER REFERENCES projects(id),
    archived INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    channel_id INTEGER NOT NULL REFERENCES channels(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    body TEXT NOT NULL,
    reply_to_id INTEGER REFERENCES messages(id),
    incident_id INTEGER REFERENCES incidents(id),
    created_at TEXT NOT NULL,
    edited_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_messages_channel ON messages(channel_id, id);
CREATE TABLE IF NOT EXISTS message_reads (
    message_id INTEGER NOT NULL REFERENCES messages(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    read_at TEXT NOT NULL,
    PRIMARY KEY (message_id, user_id)
);
CREATE TABLE IF NOT EXISTS chat_state (
    user_id INTEGER NOT NULL REFERENCES users(id),
    channel_id INTEGER NOT NULL REFERENCES channels(id),
    last_read_id INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, channel_id)
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    message_id INTEGER NOT NULL REFERENCES messages(id),
    remind_at TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

-- Planificación ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    start_date TEXT,
    target_date TEXT,
    status TEXT NOT NULL DEFAULT 'planificada',
    release_notes TEXT NOT NULL DEFAULT '',
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    row_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE (project_id, name)
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    number INTEGER NOT NULL UNIQUE,
    key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    priority TEXT NOT NULL DEFAULT 'media',
    status TEXT NOT NULL DEFAULT 'pendiente',
    block_reason TEXT,
    progress INTEGER NOT NULL DEFAULT 0,
    labels TEXT NOT NULL DEFAULT '',
    checklist TEXT NOT NULL DEFAULT '[]',
    estimate_hours REAL,
    target_date TEXT,
    planned_week TEXT,
    version_id INTEGER REFERENCES versions(id),
    idea_id INTEGER,
    assignee_id INTEGER REFERENCES users(id),
    source_message_id INTEGER,
    recurrence TEXT,
    recurrence_parent_id INTEGER,
    resolution_note TEXT NOT NULL DEFAULT '',
    last_reject_reason TEXT,
    verification_requested_at TEXT,
    verification_requested_by INTEGER,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by INTEGER NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    row_version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_tasks_project ON tasks(project_id);
CREATE INDEX IF NOT EXISTS ix_tasks_assignee ON tasks(assignee_id);
CREATE TABLE IF NOT EXISTS ideas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    priority TEXT NOT NULL DEFAULT 'media',
    version_id INTEGER REFERENCES versions(id),
    status TEXT NOT NULL DEFAULT 'propuesta',
    task_id INTEGER REFERENCES tasks(id),
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by INTEGER NOT NULL,
    row_version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS idea_votes (
    idea_id INTEGER NOT NULL REFERENCES ideas(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    PRIMARY KEY (idea_id, user_id)
);
CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    week_start TEXT NOT NULL,
    title TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    done_at TEXT,
    done_by INTEGER,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_dates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    project_id INTEGER REFERENCES projects(id),
    title TEXT NOT NULL,
    date TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS renewals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    project_id INTEGER REFERENCES projects(id),
    title TEXT NOT NULL,
    expires_on TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    owner_id INTEGER REFERENCES users(id),
    renewed_at TEXT,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Tiempo y capacidad ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS time_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    seconds INTEGER,
    note TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'timer',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_time_user ON time_entries(user_id, started_at);
CREATE INDEX IF NOT EXISTS ix_time_entity ON time_entries(entity_type, entity_id);
CREATE TABLE IF NOT EXISTS absences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS standups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    date TEXT NOT NULL,
    yesterday TEXT NOT NULL DEFAULT '',
    today TEXT NOT NULL DEFAULT '',
    blockers TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (user_id, date)
);

-- Calidad: builds, tiendas y planes de prueba --------------------------------------------
CREATE TABLE IF NOT EXISTS builds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    version_id INTEGER REFERENCES versions(id),
    number TEXT NOT NULL,
    platform TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'interno',
    built_on TEXT,
    notes TEXT NOT NULL DEFAULT '',
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (project_id, platform, number)
);
CREATE TABLE IF NOT EXISTS store_submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    build_id INTEGER NOT NULL REFERENCES builds(id),
    store TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'preparando',
    submitted_at TEXT,
    decided_at TEXT,
    released_at TEXT,
    rejection_reason TEXT NOT NULL DEFAULT '',
    rollout_percent INTEGER,
    task_id INTEGER REFERENCES tasks(id),
    notes TEXT NOT NULL DEFAULT '',
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    row_version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS test_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    suite TEXT NOT NULL DEFAULT 'General',
    title TEXT NOT NULL,
    steps TEXT NOT NULL DEFAULT '',
    expected TEXT NOT NULL DEFAULT '',
    archived INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS test_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    build_id INTEGER REFERENCES builds(id),
    version_id INTEGER REFERENCES versions(id),
    title TEXT NOT NULL,
    device TEXT NOT NULL DEFAULT '',
    os_version TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'en_curso',
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS test_results (
    run_id INTEGER NOT NULL REFERENCES test_runs(id) ON DELETE CASCADE,
    case_id INTEGER NOT NULL REFERENCES test_cases(id),
    result TEXT NOT NULL DEFAULT 'pendiente',
    note TEXT NOT NULL DEFAULT '',
    incident_id INTEGER REFERENCES incidents(id),
    updated_by INTEGER,
    updated_at TEXT,
    PRIMARY KEY (run_id, case_id)
);

-- Conocimiento: decisiones, wiki y bóveda --------------------------------------------------
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    number INTEGER NOT NULL UNIQUE,
    key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    context TEXT NOT NULL DEFAULT '',
    options TEXT NOT NULL DEFAULT '',
    decision TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'propuesta',
    superseded_by INTEGER REFERENCES decisions(id),
    source_message_id INTEGER,
    created_by INTEGER NOT NULL,
    decided_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    row_version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS wiki_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    project_id INTEGER REFERENCES projects(id),
    folder TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    source_incident_id INTEGER,
    created_by INTEGER NOT NULL,
    updated_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    row_version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS wiki_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER NOT NULL REFERENCES wiki_pages(id),
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    edited_by INTEGER NOT NULL,
    edited_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vault_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    project_id INTEGER REFERENCES projects(id),
    name TEXT NOT NULL,
    username TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    secret_enc TEXT NOT NULL,
    notes_enc TEXT NOT NULL DEFAULT '',
    allowed_roles TEXT NOT NULL DEFAULT '[]',
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vault_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER,
    user_id INTEGER,
    action TEXT NOT NULL,
    at TEXT NOT NULL
);

-- Integración con GitHub -----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS code_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    ref TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (entity_type, entity_id, kind, ref)
);

-- Incorporación --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS onboarding_templates (
    role_id INTEGER PRIMARY KEY REFERENCES roles(id) ON DELETE CASCADE,
    items TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS onboarding_progress (
    user_id INTEGER NOT NULL REFERENCES users(id),
    item TEXT NOT NULL,
    done_at TEXT,
    PRIMARY KEY (user_id, item)
);

-- Registro, avisos y sincronización ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    project_id INTEGER,
    entity_type TEXT NOT NULL,
    entity_id INTEGER,
    entity_key TEXT,
    action TEXT NOT NULL,
    summary TEXT NOT NULL,
    details TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_activity_entity ON activity(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS ix_activity_created ON activity(created_at);
CREATE INDEX IF NOT EXISTS ix_activity_user ON activity(user_id, id);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    entity_type TEXT,
    entity_id INTEGER,
    entity_key TEXT,
    urgent INTEGER NOT NULL DEFAULT 0,
    dedupe_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    read_at TEXT,
    UNIQUE (user_id, dedupe_key)
);
CREATE TABLE IF NOT EXISTS notification_prefs (
    user_id INTEGER NOT NULL REFERENCES users(id),
    type TEXT NOT NULL,
    in_app INTEGER NOT NULL DEFAULT 1,
    desktop INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (user_id, type)
);
CREATE TABLE IF NOT EXISTS changes (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    entity_type TEXT,
    entity_id INTEGER,
    user_id INTEGER,
    actor_id INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS idempotency (
    user_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    status INTEGER NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);

-- Búsqueda global (texto completo)
CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
    kind UNINDEXED, ref_id UNINDEXED, project_id UNINDEXED, key, title, body,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""

DEFAULT_SETTINGS = {
    "auto_backup_hours": 24,
    "backup_keep": 30,
    "stale_days": 5,
    "due_soon_days": 2,
    "daily_summary_hour": 9,
    "weekly_summary_weekday": 0,
    "standup_hour": 10,
    "standup_enabled": True,
    "standup_weekdays": [0, 1, 2, 3, 4],
    "sla": {
        "critica": {"response_hours": 2, "resolve_hours": 24},
        "alta": {"response_hours": 8, "resolve_hours": 72},
        "media": {"response_hours": 24, "resolve_hours": 168},
        "baja": {"response_hours": 72, "resolve_hours": 336},
    },
    "sla_enabled": True,
    "report_templates": {
        "errores": {"required": ["steps", "app_version"], "recommended": ["device", "os_version", "frequency",
                                                                            "expected", "observed"]},
        "rendimiento": {"required": ["app_version"], "recommended": ["device", "os_version", "steps"]},
        "seguridad": {"required": ["steps"], "recommended": ["app_version", "environment"]},
        "diseno": {"required": [], "recommended": ["device"]},
        "funcionalidades": {"required": [], "recommended": []},
    },
    "verification_required": True,
    "wip_limits": {},
    "github": {"repo": "", "poll_minutes": 10, "enabled": False, "last_poll": None},
    "password_min_length": 10,
    "require_2fa_roles": [],
}


def now_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def parse_iso(value: str) -> datetime:
    return datetime.strptime(value.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S.%f%z")


def seconds_between(start: str, end: str) -> int:
    return max(0, int((parse_iso(end) - parse_iso(start)).total_seconds()))


def new_uid() -> str:
    return secrets.token_hex(16)


class Database:
    def __init__(self, path: Path, before_migration=None):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.cond = threading.Condition()
        self._depth = 0
        self._dirty = False
        self.conn: sqlite3.Connection | None = None
        self.before_migration = before_migration
        self.open()

    # -- ciclo de vida ---------------------------------------------------------
    def open(self) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA foreign_keys=ON")
            self.conn = conn
            from .migrations import migrate
            migrate(self)
            with self.tx() as c:
                for k, v in DEFAULT_SETTINGS.items():
                    c.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, json.dumps(v)))
                if not c.execute("SELECT 1 FROM meta WHERE key='epoch'").fetchone():
                    c.execute("INSERT INTO meta(key, value) VALUES ('epoch', ?)", (new_uid(),))
            self.epoch = self.meta("epoch")

    def close(self) -> None:
        with self.lock:
            if self.conn is not None:
                try:
                    self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.Error:
                    pass
                self.conn.close()
                self.conn = None

    def integrity_ok(self) -> bool:
        return self.scalar("PRAGMA integrity_check") == "ok"

    # -- transacciones ---------------------------------------------------------
    @contextmanager
    def tx(self):
        with self.lock:
            if self.conn is None:
                raise RuntimeError("La base de datos está cerrada (restauración en curso).")
            outer = self._depth == 0
            if outer:
                self.conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield self.conn
            except BaseException:
                self._depth -= 1
                if outer:
                    self.conn.execute("ROLLBACK")
                    self._dirty = False
                raise
            self._depth -= 1
            if outer:
                self.conn.execute("COMMIT")
                if self._dirty:
                    self._dirty = False
                    with self.cond:
                        self.cond.notify_all()

    def query(self, sql: str, params=()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def query_one(self, sql: str, params=()) -> dict | None:
        with self.lock:
            row = self.conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def scalar(self, sql: str, params=()):
        with self.lock:
            row = self.conn.execute(sql, params).fetchone()
            return row[0] if row else None

    # -- utilidades --------------------------------------------------------------
    def meta(self, key: str) -> str | None:
        return self.scalar("SELECT value FROM meta WHERE key=?", (key,))

    def set_meta(self, conn, key: str, value: str) -> None:
        conn.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, value))

    def next_counter(self, conn: sqlite3.Connection, name: str) -> int:
        """Siguiente número de una secuencia. Nunca se reutiliza un número."""
        conn.execute("INSERT OR IGNORE INTO counters(name, value) VALUES (?, 0)", (name,))
        conn.execute("UPDATE counters SET value = value + 1 WHERE name=?", (name,))
        return conn.execute("SELECT value FROM counters WHERE name=?", (name,)).fetchone()[0]

    def emit(self, conn: sqlite3.Connection, kind: str, entity_type: str | None = None,
             entity_id: int | None = None, user_id: int | None = None, actor_id: int | None = None) -> None:
        conn.execute(
            "INSERT INTO changes(kind, entity_type, entity_id, user_id, actor_id, created_at) VALUES (?,?,?,?,?,?)",
            (kind, entity_type, entity_id, user_id, actor_id, now_iso()),
        )
        self._dirty = True

    def wake_waiters(self) -> None:
        with self.cond:
            self.cond.notify_all()

    def wait_for_changes(self, since: int, user_id: int, timeout: float) -> list[dict]:
        deadline = time.monotonic() + timeout
        while True:
            rows = self.query(
                "SELECT seq, kind, entity_type, entity_id, actor_id FROM changes "
                "WHERE seq > ? AND (user_id IS NULL OR user_id = ?) ORDER BY seq LIMIT 500",
                (since, user_id),
            )
            if rows:
                return rows
            remaining = deadline - time.monotonic()
            if remaining <= 0 or self.conn is None:
                return []
            with self.cond:
                self.cond.wait(min(remaining, 5.0))

    def get_setting(self, key: str):
        raw = self.scalar("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(raw) if raw is not None else DEFAULT_SETTINGS.get(key)

    def put_setting(self, conn, key: str, value) -> None:
        conn.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, json.dumps(value)))

    def all_settings(self) -> dict:
        out = dict(DEFAULT_SETTINGS)
        for row in self.query("SELECT key, value FROM settings"):
            out[row["key"]] = json.loads(row["value"])
        return out

    # -- búsqueda ------------------------------------------------------------------
    def index(self, conn, kind: str, ref_id: int, project_id, key: str, title: str, body: str) -> None:
        conn.execute("DELETE FROM search_index WHERE kind=? AND ref_id=?", (kind, ref_id))
        conn.execute("INSERT INTO search_index(kind, ref_id, project_id, key, title, body) VALUES (?,?,?,?,?,?)",
                     (kind, ref_id, project_id, key or "", title or "", body or ""))

    def unindex(self, conn, kind: str, ref_id: int) -> None:
        conn.execute("DELETE FROM search_index WHERE kind=? AND ref_id=?", (kind, ref_id))
