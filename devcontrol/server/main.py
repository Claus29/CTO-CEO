"""Servidor del equipo de DEV CONTROL (se ejecuta en el equipo anfitrión).

Solo escucha en redes privadas (Tailscale, VPN o red local) y rechaza cualquier
conexión que no venga de ellas. Si al arrancar no hay base de datos pero sí
copias de seguridad, NO crea una base vacía: arranca en modo recuperación para
que el anfitrión elija qué copia restaurar.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import sqlite3
import threading
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

from .. import APP_NAME, APP_VERSION, MIN_CLIENT_VERSION, PROTOCOL_VERSION, parse_version
from ..config import Config
from ..logs import setup_logging
from ..paths import Layout, default_layout
from . import (
    attachments,
    auth,
    backups,
    calls,
    chat,
    exports,
    incidents,
    integrations,
    knowledge,
    notifications,
    planning,
    quality,
    relations,
    reports,
    team,
)
from .attachments import AttachmentStore
from .backups import BackupError, BackupManager, db_member_of, run_auto_backup
from .common import BLOCK_REASONS, clean_text, log_activity, names, project_clause, visible_project_ids
from .db import Database, new_uid, now_iso
from .notifications import NotificationJobs, utc_ago
from .permissions import CATALOG
from .web import BaseHandler, HttpError, QuietServer, Response, Router, bad_request, forbidden

LONG_POLL_MAX = 30
DB_NAME = "devcontrol.db"
MODULES = (relations, incidents, attachments, chat, calls, planning, quality, knowledge, team, reports, integrations,
           notifications, backups, exports)
FEATURES = ["calls"]
# Rutas permitidas mientras el usuario debe cambiar la contraseña o activar la verificación en dos pasos.
GATE_ALLOWED = {"/api/me", "/api/me/password", "/api/logout", "/api/me/2fa/start", "/api/me/2fa/confirm",
                "/api/changes", "/api/health"}
RECOVERY_CONFIRM = "EMPEZAR VACIO"


def own_ip_addresses() -> set[str]:
    """Direcciones IP de este equipo (todas las interfaces que el sistema conoce)."""
    found = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            found.add(info[4][0].split("%")[0])
    except OSError:
        pass
    for probe in ("100.100.100.100", "10.255.255.255", "192.168.255.255"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((probe, 9))
                found.add(s.getsockname()[0])
        except OSError:
            pass
    return {ip for ip in found if ip and not ip.startswith("0.")}


class ServerApp:
    def __init__(self, cfg: Config, layout: Layout | None = None, data_dir: Path | None = None,
                 import_from: Path | None = None):
        self.cfg = cfg
        self.layout = layout or cfg.layout or default_layout()
        self.layout.ensure()
        self.data_dir = Path(data_dir or cfg.server_data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / DB_NAME
        self.log = setup_logging(self.layout.logs_dir, "servidor")
        self.backups = BackupManager(None, self.data_dir, self.layout.backups_dir)
        self.presence = auth.Presence()
        self.throttle = auth.LoginThrottle()
        self.vault_key: bytes | None = None
        self.servers: list[QuietServer] = []
        self.listening: list[str] = []
        self.listen_errors: list[str] = []
        self.stop_event = threading.Event()
        self.local_secret = self._local_secret()
        self.turn = None
        self.turn_port: int | None = None
        self.turn_error = ""
        self.turn_secret = secrets.token_bytes(32)
        self.cfg.own_addresses = own_ip_addresses()
        self._settings: dict = {}
        self._settings_epoch = None
        self._settings_lock = threading.Lock()
        self.db: Database | None = None
        self.recovery: dict | None = None
        self.router = Router()
        self.import_summary: dict | None = None
        import_error, did_import = "", False
        if not self.db_path.exists() and import_from and Path(import_from).is_file():
            # Datos anteriores elegidos en el instalador (base de datos .db o copia .zip). Solo si aún no hay datos.
            try:
                self.restore_into_empty(Path(import_from))
                did_import = True
            except (BackupError, OSError, sqlite3.Error, zipfile.BadZipFile) as exc:
                import_error = str(exc)
                self.log.error("No se pudieron importar los datos de %s: %s", import_from, exc)
                for p in self.data_dir.glob(DB_NAME + "*"):
                    p.unlink(missing_ok=True)
        if import_error:
            self.recovery = {"reason": f"No se pudieron recuperar los datos de {import_from}: {import_error} "
                                       "Elige otra copia o el archivo correcto."}
            self._register_recovery()
        elif not self.db_path.exists() and self.backups.list():
            self.recovery = {"reason": "Falta la base de datos, pero hay copias de seguridad en "
                                       f"{self.layout.backups_dir}. Elige una para restaurarla."}
            self.log.warning("No existe %s y hay copias de seguridad: modo recuperación", self.db_path)
            self._register_recovery()
        else:
            self._open()
            if did_import:
                self._record_import(Path(import_from))

    # -- arranque de la base de datos ----------------------------------------------------
    def _open(self) -> None:
        self.db = Database(self.db_path, before_migration=self._backup_before_migration)
        if not self.db.integrity_ok():
            self.log.error("La base de datos no supera la comprobación de integridad")
        self.backups.db = self.db
        self.perms = auth.PermissionCache(self.db)
        self.attachments = AttachmentStore(self)
        self.jobs = NotificationJobs(self)
        self.recovery = None
        self.router = Router()
        self._register_core()
        for module in MODULES:
            module.register(self.router, self)

    def _record_import(self, source: Path) -> None:
        counts = {t: self.db.scalar(f"SELECT COUNT(*) FROM {t}") for t in ("incidents", "tasks", "messages", "users")}
        self.import_summary = {"source": source.name, **counts}
        text = (f"Datos anteriores recuperados desde {source.name}: {counts['incidents']} incidencias, "
                f"{counts['tasks']} tareas, {counts['messages']} mensajes y {counts['users']} usuarios")
        with self.db.tx() as c:
            log_activity(self.db, c, None, "system", None, None, "import", text)
        self.log.info(text)

    def _backup_before_migration(self, db: Database, version: int) -> None:
        self.backups.db = db
        info = self.backups.create("previa-migracion")
        self.log.info("Copia previa a la migración del esquema %s: %s", version, info["name"])

    def _local_secret(self) -> str:
        p = self.data_dir / ".secreto_local"
        if not p.exists():
            p.write_text(secrets.token_urlsafe(24), encoding="utf-8")
        return p.read_text(encoding="utf-8").strip()

    # -- utilidades para los módulos ------------------------------------------------------
    @staticmethod
    def new_uid() -> str:
        return new_uid()

    def settings_cache(self, key: str):
        with self._settings_lock:
            if self._settings_epoch != self.db.epoch:
                self._settings = {}
                self._settings_epoch = self.db.epoch
            if key not in self._settings:
                self._settings[key] = self.db.get_setting(key)
            return self._settings[key]

    def settings_invalidate(self) -> None:
        with self._settings_lock:
            self._settings = {}

    # -- despacho de peticiones -----------------------------------------------------------
    def dispatch(self, handler: BaseHandler, req) -> Response:
        started = time.monotonic()
        if not self.cfg.is_allowed_client(req.client_ip):
            self.log.warning("Conexión rechazada desde %s (fuera de las redes permitidas)", req.client_ip)
            raise HttpError(403, "red_no_autorizada", "Este equipo no está autorizado para conectarse a este servidor.")
        client_version = req.headers.get("x-devcontrol-version")
        if client_version and parse_version(client_version) < parse_version(MIN_CLIENT_VERSION) \
                and req.path not in ("/api/health", "/api/update/manifest", "/api/update/installer"):
            raise HttpError(426, "actualizacion_obligatoria",
                            f"Este servidor necesita DEV CONTROL {MIN_CLIENT_VERSION} o posterior. Actualiza el programa.",
                            {"min_version": MIN_CLIENT_VERSION, "server_version": APP_VERSION})
        route, params = self.router.match(req.method if req.method != "HEAD" else "GET", req.path)
        req.params = params
        if self.db is not None and getattr(self, "_perm_epoch", None) != self.db.epoch:
            self._perm_epoch = self.db.epoch
            self.perms.invalidate()
        if route.auth:
            authz = req.headers.get("authorization", "")
            token = authz[7:].strip() if authz.lower().startswith("bearer ") else ""
            resolved = auth.resolve_session(self.db, token, self.perms) if token else None
            if not resolved:
                raise HttpError(401, "sesion_no_valida", "Tu sesión ha caducado o no es válida. Vuelve a iniciar sesión.")
            req.user, req.session_hash = resolved
            self._touch(req.user, req.session_hash)
            gate = self._gate(req.user)
            if gate and req.path not in GATE_ALLOWED:
                raise HttpError(403, gate[0], gate[1])
            if route.perm:
                auth.require(req.user, route.perm)
        handler.read_body(req, route.upload, self.data_dir / "tmp", self.cfg.max_upload_mb * 1024 * 1024)
        key = req.headers.get("idempotency-key", "").strip()[:80]
        try:
            if route.idempotent and key and req.user:
                with self.db.lock:
                    stored = self.db.query_one("SELECT status, body FROM idempotency WHERE user_id=? AND key=?",
                                               (req.user["id"], key))
                    if stored:
                        resp = Response(stored["status"], stored["body"].encode("utf-8"),
                                        {"Content-Type": "application/json; charset=utf-8", "X-Idempotent-Replay": "1"})
                    else:
                        with self.db.tx() as c:
                            resp = self._call(route, req, params)
                            if resp.status < 300 and resp.file_path is None:
                                c.execute("INSERT INTO idempotency(user_id, key, status, body, created_at) "
                                          "VALUES (?,?,?,?,?)", (req.user["id"], key, resp.status,
                                                                 resp.body.decode("utf-8"), now_iso()))
            else:
                resp = self._call(route, req, params)
        except HttpError as exc:
            self.log.info("%s %s -> %s %s", req.method, req.path, exc.status, exc.code)
            raise
        except Exception:
            self.log.exception("Error inesperado en %s %s", req.method, req.path)
            raise HttpError(500, "error_interno", "Error interno del servidor. El detalle se ha guardado en el "
                                                  "registro del servidor (Registros\\servidor.log).")
        if req.path != "/api/changes":
            self.log.info("%s %s -> %s (%d ms) %s", req.method, req.path, resp.status,
                          (time.monotonic() - started) * 1000, req.user["username"] if req.user else "-")
        return resp

    def _gate(self, user: dict) -> tuple[str, str] | None:
        if user.get("must_change_password"):
            return "debe_cambiar_contrasena", "Debes cambiar tu contraseña antes de continuar."
        if not user.get("totp_enabled") and user["role_key"] in (self.settings_cache("require_2fa_roles") or []):
            return "debe_activar_2fa", "Tu rol exige la verificación en dos pasos. Actívala para continuar."
        return None

    @staticmethod
    def _call(route, req, params) -> Response:
        result = route.handler(req, **params)
        return result if isinstance(result, Response) else Response.json(result)

    def _touch(self, user: dict, session_hash: str | None = None) -> None:
        was_online = self.presence.is_online(user["id"])
        self.presence.touch(user["id"])
        if not was_online:
            with self.db.tx() as c:
                if session_hash:
                    c.execute("UPDATE sessions SET last_used_at=? WHERE token_hash=?", (now_iso(), session_hash))
                self.db.emit(c, "presence", "user", user["id"])

    def roles_map(self) -> dict:
        return {r["id"]: r for r in self.db.query("SELECT id, key, name FROM roles")}

    # -- modo recuperación ------------------------------------------------------------------
    def _register_recovery(self) -> None:
        r = self.router

        def host_only(req):
            if not self.cfg.is_host_itself(req.client_ip):
                raise forbidden("La recuperación solo puede hacerse desde el equipo anfitrión.")

        @r.route("GET", "/api/health", auth=False)
        def health(req):
            return self._health_payload()

        @r.route("GET", "/api/recovery", auth=False)
        def recovery_info(req):
            host_only(req)
            return {"recovery": self.recovery, "backups": self.backups.list(),
                    "backups_dir": str(self.layout.backups_dir), "data_dir": str(self.data_dir)}

        @r.route("POST", "/api/recovery/restore", auth=False)
        def recovery_restore(req):
            host_only(req)
            data = req.json
            if data.get("name"):
                try:
                    path = self.backups._path(str(data["name"]))
                except BackupError as exc:
                    raise bad_request(str(exc)) from None
            else:
                path = Path(str(data.get("path") or "")).expanduser()
                if not path.is_file():
                    raise bad_request("No se encuentra el archivo indicado.")
            try:
                self.restore_into_empty(path)
            except BackupError as exc:
                raise bad_request(str(exc)) from None
            self._open()
            with self.db.tx() as c:
                log_activity(self.db, c, None, "backup", None, path.name, "restored",
                             f"Datos recuperados desde {path.name} (no existía la base de datos)")
            self.log.info("Recuperación completada desde %s", path)
            return {"ok": True, "health": self._health_payload()}

        @r.route("POST", "/api/recovery/start-empty", auth=False)
        def recovery_empty(req):
            host_only(req)
            if str(req.json.get("confirm") or "").strip().upper() != RECOVERY_CONFIRM:
                raise bad_request(f"Escribe «{RECOVERY_CONFIRM}» para confirmar que quieres empezar sin datos.")
            self._open()
            self.log.warning("El anfitrión eligió empezar con una base de datos vacía pese a existir copias")
            return {"ok": True, "health": self._health_payload()}

        @r.route("POST", "/internal/shutdown", auth=False)
        def shutdown(req):
            return self._internal_shutdown(req)

    def restore_into_empty(self, path: Path) -> None:
        """Coloca la base de datos (y los adjuntos) de una copia .zip o un archivo .db de la versión 1."""
        tmp = self.data_dir / f"recuperando-{new_uid()[:8]}"
        tmp.mkdir(parents=True)
        try:
            if path.suffix.lower() == ".zip":
                self.backups.validate(path)
                with zipfile.ZipFile(path) as zf:
                    member = db_member_of(set(zf.namelist()))
                    zf.extract(member, tmp)
                    for n in zf.namelist():
                        if n.startswith("adjuntos/") and not n.endswith("/"):
                            zf.extract(n, self.data_dir)
                src = tmp / member
            else:
                src = tmp / "importado.db"
                source = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
                try:
                    dst = sqlite3.connect(str(src))
                    source.backup(dst)
                    dst.close()
                finally:
                    source.close()
                old_att = path.parent / "adjuntos"
                if old_att.is_dir() and not (self.data_dir / "adjuntos").exists():
                    shutil.copytree(old_att, self.data_dir / "adjuntos")
            conn = sqlite3.connect(str(src))
            try:
                if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise BackupError("La base de datos elegida está dañada.")
            except sqlite3.DatabaseError as exc:
                raise BackupError(f"El archivo no es una base de datos válida: {exc}") from None
            finally:
                conn.close()
            os.replace(src, self.db_path)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # -- rutas básicas ------------------------------------------------------------------------
    def _health_payload(self) -> dict:
        out = {"ok": True, "app": APP_NAME, "version": APP_VERSION, "protocol_version": PROTOCOL_VERSION,
               "min_client_version": MIN_CLIENT_VERSION, "server_time": now_iso(), "server_name": socket.gethostname(),
               "listening": self.listening, "listen_errors": self.listen_errors}
        if self.db is None:
            out.update(recovery=True, recovery_reason=self.recovery["reason"], setup_required=False, epoch=None)
        else:
            out.update(recovery=False, epoch=self.db.epoch, features=FEATURES, call_port=self.turn_port,
                       call_error=self.turn_error,
                       setup_required=not self.db.scalar("SELECT COUNT(*) FROM users"),
                       imported=self.import_summary)
        return out

    def _internal_shutdown(self, req):
        if not req.is_loopback or req.headers.get("x-devcontrol-local-secret") != self.local_secret:
            raise forbidden()
        threading.Thread(target=self.shutdown, daemon=True).start()
        return {"ok": True}

    def _register_core(self):
        r = self.router
        db = self.db

        @r.route("GET", "/api/health", auth=False)
        def health(req):
            return self._health_payload()

        @r.route("POST", "/internal/shutdown", auth=False)
        def shutdown(req):
            return self._internal_shutdown(req)

        # -- actualizaciones para los demás equipos (repositorio privado: solo el anfitrión accede a GitHub) ----
        @r.route("GET", "/api/update/manifest", auth=False)
        def update_manifest(req):
            folder = self.layout.updates_dir
            raw, sig = folder / "manifest.json", folder / "manifest.json.sig"
            if not raw.exists() or not sig.exists():
                raise HttpError(404, "sin_version", "El anfitrión aún no conoce ninguna versión publicada.")
            import base64
            return {"manifest": base64.b64encode(raw.read_bytes()).decode(),
                    "signature": sig.read_text(encoding="ascii").strip()}

        @r.route("GET", "/api/update/installer", auth=False)
        def update_installer(req):
            folder = self.layout.updates_dir
            try:
                manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
                name = Path(manifest["installer"]["name"]).name
            except (OSError, ValueError, KeyError, TypeError):
                raise HttpError(404, "sin_version", "El anfitrión aún no conoce ninguna versión publicada.") from None
            path = folder / name
            if not path.is_file():
                raise HttpError(404, "sin_instalador", "El anfitrión todavía no ha descargado el instalador.")
            # Cada equipo comprueba la firma del manifiesto y la huella SHA-256 antes de instalar.
            return Response.file(path, name, "application/octet-stream")

        @r.route("GET", "/api/permissions/catalog")
        def catalog(req):
            return {"items": [{"key": k, "area": a, "label": label} for k, a, label in CATALOG]}

        # -- configuración inicial ---------------------------------------------------
        @r.route("POST", "/api/setup", auth=False)
        def setup(req):
            if not self.cfg.is_host_itself(req.client_ip):
                self.log.warning("Configuración inicial rechazada desde %s", req.client_ip)
                raise forbidden("La configuración inicial solo puede hacerse desde el equipo anfitrión "
                                f"(esta petición llega desde {req.client_ip}). Abre DEV CONTROL en el anfitrión.")
            data = req.json
            admin = data.get("admin") or {}
            username = clean_text(admin.get("username"), "usuario", 40, required=True).lower()
            if not username.replace(".", "").replace("_", "").replace("-", "").isalnum():
                raise bad_request("El usuario solo puede tener letras, números, puntos, guiones y guiones bajos.")
            display = clean_text(admin.get("display_name"), "nombre", 60, required=True)
            auth.validate_new_password(admin.get("password"), db.get_setting("password_min_length"), username)
            pkey = clean_text(data.get("project_key") or "INC", "prefijo", 8).upper()
            if not pkey.isalnum() or not pkey[0].isalpha() or pkey in ("T", "DEC"):
                raise bad_request("El prefijo del proyecto debe ser de letras y números (por ejemplo INC o APP) y "
                                  "no puede ser T ni DEC.")
            pname = clean_text(data.get("project_name") or "General", "proyecto", 60, required=True)
            ts = now_iso()
            with db.tx() as c:
                if c.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
                    raise HttpError(409, "ya_configurado", "DEV CONTROL ya está configurado.")
                role_id = c.execute("SELECT id FROM roles WHERE key='admin'").fetchone()[0]
                uid = c.execute("INSERT INTO users(username, display_name, email, role_id, password_hash, created_at) "
                                "VALUES (?,?,?,?,?,?)", (username, display, clean_text(admin.get("email"), "correo", 120),
                                                         role_id, auth.hash_password(admin["password"]), ts)).lastrowid
                row = c.execute("SELECT id FROM projects ORDER BY id LIMIT 1").fetchone()
                if row:
                    pid = row[0]
                else:
                    pid = c.execute("INSERT INTO projects(uid, key, name, created_by, created_at, updated_at) "
                                    "VALUES (?,?,?,?,?,?)", (new_uid(), pkey, pname, uid, ts, ts)).lastrowid
                    db.set_meta(c, "default_project", str(pid))
                c.execute("INSERT OR IGNORE INTO project_members(project_id, user_id) VALUES (?, ?)", (pid, uid))
                if not c.execute("SELECT 1 FROM channels WHERE kind='general'").fetchone():
                    c.execute("INSERT INTO channels(uid, name, kind, created_at) VALUES (?, 'General', 'general', ?)",
                              (new_uid(), ts))
                log_activity(db, c, {"id": uid}, "system", None, None, "setup",
                             f"Configuración inicial: administrador {username}, proyecto {pkey}", project_id=pid)
            self.log.info("Configuración inicial completada (administrador %s)", username)
            return {"ok": True, "username": username}

        # -- sesión ------------------------------------------------------------------
        @r.route("POST", "/api/login", auth=False)
        def login(req):
            data = req.json
            username = str(data.get("username") or "").strip().lower()
            throttle_key = f"{req.client_ip}|{username}"
            self.throttle.check(req.client_ip)
            self.throttle.check(throttle_key)
            password = str(data.get("password") or "")
            row = db.query_one("SELECT u.*, r.key AS role_key, r.name AS role_name FROM users u JOIN roles r "
                               "ON r.id=u.role_id WHERE u.username=? AND u.active=1", (username,))
            if not row or not auth.verify_password(password, row["password_hash"]):
                self.throttle.fail(req.client_ip)
                self.throttle.fail(throttle_key)
                self.log.warning("Inicio de sesión fallido para '%s' desde %s", username, req.client_ip)
                raise HttpError(401, "credenciales_incorrectas", "Usuario o contraseña incorrectos.")
            if row["totp_enabled"]:
                code = str(data.get("totp_code") or "").strip()
                recovery = str(data.get("recovery_code") or "").strip()
                if not code and not recovery:
                    raise HttpError(401, "requiere_2fa", "Escribe el código de 6 cifras de tu aplicación de "
                                                         "autenticación.")
                if code and auth.verify_totp(row["totp_secret"], code):
                    pass
                elif recovery and (remaining := auth.use_recovery_code(row["recovery_codes"], recovery)) is not None:
                    with db.tx() as c:
                        c.execute("UPDATE users SET recovery_codes=? WHERE id=?", (remaining, row["id"]))
                        log_activity(db, c, row, "user", row["id"], row["username"], "recovery_code",
                                     "Inició sesión con un código de recuperación")
                else:
                    self.throttle.fail(throttle_key)
                    raise HttpError(401, "codigo_2fa_incorrecto", "El código de verificación no es correcto.",
                                    {"requires_2fa": True})
            self.throttle.reset(req.client_ip)
            self.throttle.reset(throttle_key)
            label = clean_text(data.get("client_label"), "equipo", 80)
            token, expires = auth.create_session(db, row["id"], bool(data.get("remember")), req.client_ip, label)
            self._touch(row)
            self.log.info("Inicio de sesión de %s desde %s", username, req.client_ip)
            row["permissions"] = self.perms.for_role(row["role_id"])
            return {"token": token, "expires_at": expires, **me_payload(row)}

        @r.route("POST", "/api/logout")
        def logout(req):
            with db.tx() as c:
                c.execute("DELETE FROM sessions WHERE token_hash=?", (req.session_hash,))
            return {"ok": True}

        def me_payload(user: dict) -> dict:
            ids = visible_project_ids(db, user)
            projects = db.query("SELECT id, key, name, color, archived FROM projects ORDER BY archived, name")
            if ids is not None:
                projects = [p for p in projects if p["id"] in ids]
            onboarding = db.query_one("SELECT items FROM onboarding_templates WHERE role_id=?", (user["role_id"],))
            items = json.loads(onboarding["items"]) if onboarding else []
            done = {x["item"] for x in db.query("SELECT item FROM onboarding_progress WHERE user_id=? AND done_at IS "
                                                "NOT NULL", (user["id"],))}
            gate = self._gate(user)
            settings = db.all_settings()
            default_pid = None
            try:
                from .common import default_project_id
                default_pid = default_project_id(db, user) if projects else None
            except HttpError:
                pass
            return {
                "user": auth.public_user(user, self.presence),
                "permissions": sorted(user["permissions"]),
                "projects": projects, "default_project_id": default_pid,
                "gate": {"code": gate[0], "message": gate[1]} if gate else None,
                "onboarding_pending": len([i for i in items if i not in done]),
                "settings": {k: settings.get(k) for k in ("verification_required", "report_templates", "sla",
                                                          "sla_enabled", "wip_limits", "password_min_length")},
                "block_reasons": BLOCK_REASONS,
                "epoch": db.epoch, "server_version": APP_VERSION,
            }

        @r.route("GET", "/api/me")
        def me(req):
            return me_payload(req.user)

        @r.route("POST", "/api/me/password")
        def change_password(req):
            data = req.json
            if not auth.verify_password(str(data.get("current") or ""), req.user["password_hash"]):
                raise HttpError(400, "contrasena_actual_incorrecta", "La contraseña actual no es correcta.")
            auth.validate_new_password(data.get("new_password"), db.get_setting("password_min_length"),
                                       req.user["username"])
            if data["new_password"] == data.get("current"):
                raise bad_request("La nueva contraseña debe ser distinta de la actual.")
            with db.tx() as c:
                c.execute("UPDATE users SET password_hash=?, must_change_password=0 WHERE id=?",
                          (auth.hash_password(data["new_password"]), req.user["id"]))
                c.execute("DELETE FROM sessions WHERE user_id=? AND token_hash<>?", (req.user["id"], req.session_hash))
                log_activity(db, c, req.user, "user", req.user["id"], req.user["username"], "password",
                             "Cambió su contraseña")
            return {"ok": True}

        @r.route("PATCH", "/api/me")
        def edit_me(req):
            data = req.json
            fields = {}
            if "display_name" in data:
                fields["display_name"] = clean_text(data["display_name"], "nombre", 60, required=True)
            if "email" in data:
                fields["email"] = clean_text(data["email"], "correo", 120)
            if "mute_until" in data:
                fields["mute_until"] = data["mute_until"] or None
            if fields:
                with db.tx() as c:
                    c.execute(f"UPDATE users SET {', '.join(k + '=?' for k in fields)} WHERE id=?",
                              list(fields.values()) + [req.user["id"]])
                    db.emit(c, "user", "user", req.user["id"])
            return {"ok": True}

        # -- verificación en dos pasos ---------------------------------------------------
        @r.route("POST", "/api/me/2fa/start")
        def totp_start(req):
            if req.user["totp_enabled"]:
                raise bad_request("La verificación en dos pasos ya está activada.")
            secret = auth.new_totp_secret()
            with db.tx() as c:
                c.execute("UPDATE users SET totp_secret=? WHERE id=?", (secret, req.user["id"]))
            return {"secret": secret, "uri": auth.otpauth_uri(secret, req.user["username"])}

        @r.route("POST", "/api/me/2fa/confirm")
        def totp_confirm(req):
            secret = req.user.get("totp_secret")
            if req.user["totp_enabled"] or not secret:
                raise bad_request("Empieza de nuevo la activación de la verificación en dos pasos.")
            if not auth.verify_totp(secret, str(req.json.get("code") or "")):
                raise bad_request("El código no es correcto. Comprueba que la hora del móvil es la correcta.")
            codes, stored = auth.new_recovery_codes()
            with db.tx() as c:
                c.execute("UPDATE users SET totp_enabled=1, recovery_codes=? WHERE id=?", (stored, req.user["id"]))
                log_activity(db, c, req.user, "user", req.user["id"], req.user["username"], "2fa_on",
                             "Activó la verificación en dos pasos")
            return {"ok": True, "recovery_codes": codes}

        @r.route("POST", "/api/me/2fa/disable")
        def totp_disable(req):
            if not auth.verify_password(str(req.json.get("password") or ""), req.user["password_hash"]):
                raise HttpError(400, "contrasena_actual_incorrecta", "La contraseña no es correcta.")
            if req.user["role_key"] in (self.settings_cache("require_2fa_roles") or []):
                raise bad_request("Tu rol exige la verificación en dos pasos; no se puede desactivar.")
            with db.tx() as c:
                c.execute("UPDATE users SET totp_enabled=0, totp_secret=NULL, recovery_codes=NULL WHERE id=?",
                          (req.user["id"],))
                log_activity(db, c, req.user, "user", req.user["id"], req.user["username"], "2fa_off",
                             "Desactivó la verificación en dos pasos")
            return {"ok": True}

        # -- sesiones abiertas ---------------------------------------------------------------
        @r.route("GET", "/api/me/sessions")
        def my_sessions(req):
            rows = db.query("SELECT token_hash, created_at, expires_at, remember, client_ip, client_label, last_used_at "
                            "FROM sessions WHERE user_id=? AND expires_at > ? ORDER BY last_used_at DESC",
                            (req.user["id"], now_iso()))
            return {"items": [{"id": s["token_hash"][:16], "current": s["token_hash"] == req.session_hash,
                               **{k: s[k] for k in s if k != "token_hash"}} for s in rows]}

        @r.route("DELETE", "/api/me/sessions/{sid}")
        def close_session(req, sid):
            if len(sid) != 16:
                raise bad_request("Sesión no válida.")
            with db.tx() as c:
                c.execute("DELETE FROM sessions WHERE user_id=? AND substr(token_hash, 1, 16)=? AND token_hash<>?",
                          (req.user["id"], sid, req.session_hash))
            return {"ok": True}

        # -- equipo -------------------------------------------------------------------------
        @r.route("GET", "/api/users")
        def users(req):
            roles = self.roles_map()
            rows = db.query("SELECT * FROM users WHERE active=1 ORDER BY display_name")
            members = {}
            for m in db.query("SELECT project_id, user_id FROM project_members"):
                members.setdefault(m["user_id"], []).append(m["project_id"])
            out = []
            for u in rows:
                pu = auth.public_user(u, self.presence, roles)
                pu["project_ids"] = members.get(u["id"], [])
                pu["all_projects"] = "proyectos.ver_todos" in self.perms.for_role(u["role_id"])
                out.append(pu)
            return {"items": out}

        @r.route("GET", "/api/changes")
        def changes(req):
            since = req.q_int("since", 0)
            wait = min(max(req.q_int("wait", 25), 0), LONG_POLL_MAX)
            epoch = req.q("epoch")
            max_seq = db.scalar("SELECT COALESCE(MAX(seq), 0) FROM changes")
            min_seq = db.scalar("SELECT COALESCE(MIN(seq), 0) FROM changes")
            reset = bool(epoch and epoch != db.epoch) or since > max_seq or bool(since and min_seq and since < min_seq - 1)
            if reset or since == 0:
                rows, seq = [], max_seq
            else:
                rows = db.wait_for_changes(since, req.user["id"], wait)
                seq = rows[-1]["seq"] if rows else since
                reset = reset or any(row["kind"] == "reset" for row in rows)
            self.presence.touch(req.user["id"])
            return {"epoch": db.epoch, "seq": seq, "reset": bool(reset), "changes": rows,
                    "presence": [{"id": uid, "online": self.presence.is_online(uid),
                                  "last_seen_at": self.presence.last_seen_iso(uid)}
                                 for uid in [u["id"] for u in db.query("SELECT id FROM users WHERE active=1")]]}

        # -- panel ------------------------------------------------------------------------------
        @r.route("GET", "/api/dashboard")
        def dashboard(req):
            user = req.user
            perms = user["permissions"]
            today = date.today().isoformat()
            soon = (date.today() + timedelta(days=14)).isoformat()
            week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
            un = names(db.conn)
            iclause, iparams = project_clause(db, user, "i.project_id", req)
            tclause, tparams = project_clause(db, user, "t.project_id", req)
            can_inc = "incidencias.ver" in perms
            can_task = "tareas.ver" in perms
            cols = ("i.id, i.key, i.title, i.priority, i.status, i.category, i.target_date, i.created_at, i.updated_at, "
                    "i.created_by, i.assignee_id, i.block_reason_code, i.block_reason_text, i.blocked_at, i.resolved_at, "
                    "i.verification_requested_at, i.verification_requested_by, i.project_id, p.key AS project_key, "
                    "p.color AS project_color")
            open_inc = "i.status NOT IN ('resuelta', 'duplicada')"

            def inc(where: str, params=(), order="i.created_at DESC", limit=10) -> list[dict]:
                if not can_inc:
                    return []
                rows = db.query(f"SELECT {cols} FROM incidents i JOIN projects p ON p.id=i.project_id WHERE {iclause} "
                                f"AND {where} ORDER BY {order} LIMIT {limit}", iparams + list(params))
                for x in rows:
                    x["type"] = "incident"
                    x["created_by_name"] = un.get(x["created_by"])
                    x["assignee_name"] = un.get(x["assignee_id"])
                    if x["block_reason_code"]:
                        x["block_reason_label"] = BLOCK_REASONS.get(x["block_reason_code"])
                return rows

            def tasks(where: str, params=(), order="t.updated_at DESC", limit=10) -> list[dict]:
                if not can_task:
                    return []
                rows = db.query(f"SELECT t.id, t.key, t.title, t.priority, t.status, t.target_date, t.progress, "
                                f"t.assignee_id, t.created_by, t.block_reason, t.verification_requested_at, "
                                f"t.verification_requested_by, t.project_id, p.key AS project_key, p.color AS "
                                f"project_color FROM tasks t JOIN projects p ON p.id=t.project_id WHERE {tclause} AND "
                                f"{where} ORDER BY {order} LIMIT {limit}", tparams + list(params))
                for x in rows:
                    x["type"] = "task"
                    x["created_by_name"] = un.get(x["created_by"])
                    x["assignee_name"] = un.get(x["assignee_id"])
                return rows

            def count_inc(where: str, params=()) -> int:
                if not can_inc:
                    return 0
                return db.scalar(f"SELECT COUNT(*) FROM incidents i WHERE {iclause} AND {where}",
                                 iparams + list(params)) or 0

            def count_task(where: str, params=()) -> int:
                if not can_task:
                    return 0
                return db.scalar(f"SELECT COUNT(*) FROM tasks t WHERE {tclause} AND {where}", tparams + list(params)) or 0

            uid = user["id"]
            verify_any_inc = "incidencias.verificar_todas" in perms
            verify_any_task = "tareas.verificar_todas" in perms
            to_verify = inc("i.status='en_verificacion'" + ("" if verify_any_inc else " AND i.created_by=?"),
                            () if verify_any_inc else (uid,), "i.verification_requested_at", 20) + \
                tasks("t.status='en_verificacion'" + ("" if verify_any_task else " AND t.created_by=?"),
                      () if verify_any_task else (uid,), "t.verification_requested_at", 20)
            mine = inc(f"{open_inc} AND i.status<>'en_verificacion' AND i.assignee_id=?", (uid,),
                       "CASE i.priority WHEN 'critica' THEN 0 WHEN 'alta' THEN 1 WHEN 'media' THEN 2 ELSE 3 END, "
                       "i.created_at", 15) + \
                tasks("t.status NOT IN ('resuelto', 'en_verificacion') AND t.assignee_id=?", (uid,),
                      "CASE t.priority WHEN 'critica' THEN 0 WHEN 'alta' THEN 1 WHEN 'media' THEN 2 ELSE 3 END, "
                      "t.target_date IS NULL, t.target_date", 15)
            unseen = inc(f"{open_inc} AND i.assignee_id=? AND NOT EXISTS (SELECT 1 FROM incident_views v WHERE "
                         f"v.incident_id=i.id AND v.user_id=?)", (uid, uid))
            waiting_mine = inc("i.status='en_verificacion' AND i.verification_requested_by=?", (uid,)) + \
                tasks("t.status='en_verificacion' AND t.verification_requested_by=?", (uid,))
            counts = {
                "open": count_inc(open_inc),
                "critical": count_inc(f"{open_inc} AND i.priority='critica'"),
                "high": count_inc(f"{open_inc} AND i.priority='alta'"),
                "blocked": count_inc("i.status='bloqueada'") + count_task("t.status='bloqueada'"),
                "overdue": count_inc(f"{open_inc} AND i.target_date < ?", (today,)) +
                count_task("t.status NOT IN ('resuelto') AND t.target_date < ?", (today,)),
                "to_verify": len(to_verify),
                "unassigned": count_inc(f"{open_inc} AND i.assignee_id IS NULL"),
                "in_progress": count_inc("i.status='en_progreso'"),
                "resolved_week": count_inc("i.status='resuelta' AND i.resolved_at >= ?", (week_ago,)),
                "tasks_pending": count_task("t.status='pendiente'"),
                "tasks_in_progress": count_task("t.status='en_progreso'"),
                "unread_messages": self.chat_unread(user) if "chat.usar" in perms else 0,
                "unread_notifications": db.scalar("SELECT COUNT(*) FROM notifications WHERE user_id=? AND read_at IS "
                                                  "NULL", (uid,)),
            }
            upcoming = []
            if can_inc or can_task:
                parts, params = [], []
                if can_inc:
                    parts.append(f"SELECT 'incident' AS type, i.id, i.key, i.title, i.target_date, i.priority, i.status "
                                 f"FROM incidents i WHERE {iclause} AND {open_inc} AND i.target_date BETWEEN ? AND ?")
                    params += iparams + [today, soon]
                if can_task:
                    parts.append(f"SELECT 'task', t.id, t.key, t.title, t.target_date, t.priority, t.status FROM tasks t "
                                 f"WHERE {tclause} AND t.status<>'resuelto' AND t.target_date BETWEEN ? AND ?")
                    params += tparams + [today, soon]
                upcoming = db.query(" UNION ALL ".join(parts) + " ORDER BY target_date LIMIT 12", params)
            return {
                "counts": counts, "to_verify": to_verify, "mine": mine, "unseen": unseen,
                "waiting_verification": waiting_mine,
                "blocked": inc("i.status='bloqueada'", (), "i.blocked_at") + tasks("t.status='bloqueada'"),
                "priority_open": inc(f"{open_inc} AND i.priority IN ('critica','alta')", (),
                                     "CASE i.priority WHEN 'critica' THEN 0 ELSE 1 END, i.created_at"),
                "overdue": inc(f"{open_inc} AND i.target_date < ?", (today,), "i.target_date") +
                tasks("t.status<>'resuelto' AND t.target_date < ?", (today,), "t.target_date"),
                "resolved_recent": inc("i.status='resuelta'", (), "i.resolved_at DESC", 6),
                "upcoming": upcoming,
            }

        # -- actividad ---------------------------------------------------------------------------
        @r.route("GET", "/api/activity")
        def activity(req):
            auth.require_any(req.user, "actividad.ver_propia", "actividad.ver_todos")
            sql, params = self.activity_query(req)
            limit = min(req.q_int("limit", 100), 500)
            offset = req.q_int("offset", 0)
            rows = db.query(sql + " LIMIT ? OFFSET ?", params + [limit + 1, offset])
            un = names(db.conn)
            for a in rows:
                a["user_name"] = un.get(a["user_id"], "Sistema")
                a["details"] = json.loads(a["details"]) if a["details"] else None
            return {"items": rows[:limit], "has_more": len(rows) > limit,
                    "all_users": "actividad.ver_todos" in req.user["permissions"]}

    # -- arranque ----------------------------------------------------------------------------
    def _make_handler(self):
        app = self

        class Handler(BaseHandler):
            pass
        Handler.app = app
        return Handler

    def _serve(self, host: str, port: int) -> QuietServer:
        srv = QuietServer((host, port), self._make_handler())
        threading.Thread(target=srv.serve_forever, name=f"http-{host}", daemon=True).start()
        self.servers.append(srv)
        return srv

    def start(self, port: int | None = None, listen: list[str] | None = None) -> int:
        """Escucha en el puerto indicado. listen=["auto"] escucha en todas las interfaces y filtra
        por red privada; una lista de IP escucha solo en ellas (y en 127.0.0.1)."""
        port = self.cfg.server_port if port is None else port
        listen = listen if listen is not None else self.cfg.listen
        if "auto" in listen:
            srv = self._serve("0.0.0.0", port)
            self.port = srv.server_address[1]
            self.listening = [f"0.0.0.0:{self.port}"]
        else:
            srv = self._serve("127.0.0.1", port)
            self.port = srv.server_address[1]
            self.listening = [f"127.0.0.1:{self.port}"]
            for ip in listen:
                if ip in ("127.0.0.1", "localhost", "::1"):
                    continue
                threading.Thread(target=self._bind_loop, args=(ip,), name=f"bind-{ip}", daemon=True).start()
        self.log.info("%s %s escuchando en %s (datos: %s)", APP_NAME, APP_VERSION, ", ".join(self.listening),
                      self.data_dir)
        threading.Thread(target=self._scheduler, name="programador", daemon=True).start()
        self._start_turn(0 if port == 0 else self.cfg.call_port)
        return self.port

    # -- repetidor de llamadas ---------------------------------------------------------------
    def _start_turn(self, port: int) -> None:
        from .turn import TurnServer

        def authorize(user_id: int, call_id: int) -> bool:
            hub = getattr(self, "calls", None)
            if hub is None:
                return False
            with hub.lock:
                call = hub.calls.get(call_id)
                p = call["parts"].get(user_id) if call else None
                return bool(p and p["state"] in ("dentro", "llamando"))

        try:
            self.turn = TurnServer(port, self.turn_secret, self.cfg.is_allowed_client, authorize,
                                   port_range=self.cfg.relay_ports, log=self.log)
            self.turn.start()
            self.turn_port = self.turn.port
            self.log.info("Repetidor de llamadas en el puerto %s (UDP y TCP)", self.turn_port)
        except OSError as exc:
            self.turn, self.turn_port = None, None
            self.turn_error = (f"No se puede abrir el puerto {port} para las llamadas ({exc}). Las llamadas funcionarán "
                               "solo entre equipos que puedan verse directamente.")
            self.log.error(self.turn_error)

    def turn_drop(self, call_id: int) -> None:
        if self.turn:
            self.turn.drop_call(call_id)

    def _bind_loop(self, ip: str):
        """Escucha en una IP concreta; si la red (p. ej. Tailscale) aún no está activa, lo reintenta."""
        last_error = ""
        while not self.stop_event.is_set():
            try:
                self._serve(ip, self.port)
                self.listening.append(f"{ip}:{self.port}")
                self.listen_errors = [e for e in self.listen_errors if not e.startswith(ip)]
                self.log.info("Escuchando también en %s:%s", ip, self.port)
                return
            except OSError as exc:
                msg = f"{ip}: no se puede escuchar ({exc}). ¿Está conectada esa red?"
                if msg != last_error:
                    self.log.warning(msg)
                    self.listen_errors = [e for e in self.listen_errors if not e.startswith(ip)] + [msg]
                last_error = msg
            self.stop_event.wait(10)

    def _scheduler(self):
        last_jobs = last_backup = last_prune = last_github = last_addr = 0.0
        known_online: set[int] = set()
        while not self.stop_event.wait(5):
            if self.db is None:
                continue
            try:
                online = self.presence.online_ids()
                if online != known_online:
                    with self.db.tx() as c:
                        for uid in online ^ known_online:
                            self.db.emit(c, "presence", "user", uid)
                            if uid not in online:
                                c.execute("UPDATE users SET last_seen_at=? WHERE id=?",
                                          (self.presence.last_seen_iso(uid), uid))
                    known_online = online
                now = time.monotonic()
                if now - last_addr >= 60:
                    last_addr = now
                    self.cfg.own_addresses = own_ip_addresses()
                if now - last_jobs >= 60:
                    last_jobs = now
                    self.jobs.run()
                if now - last_backup >= 600:
                    last_backup = now
                    run_auto_backup(self)
                if now - last_github >= 60:
                    last_github = now
                    threading.Thread(target=self._github_poll_safe, name="github", daemon=True).start()
                if now - last_prune >= 3600:
                    last_prune = now
                    self.prune()
            except Exception:
                self.log.exception("Error en las tareas programadas")

    def _github_poll_safe(self):
        try:
            self.github_poll()
        except Exception:
            self.log.exception("Error al consultar GitHub")

    def prune(self):
        cutoff = utc_ago(7)
        with self.db.tx() as c:
            c.execute("DELETE FROM changes WHERE created_at < ? AND seq < (SELECT MAX(seq) FROM changes)", (cutoff,))
            c.execute("DELETE FROM idempotency WHERE created_at < ?", (cutoff,))
            c.execute("DELETE FROM sessions WHERE expires_at < ?", (now_iso(),))
        tmp = self.data_dir / "tmp"
        if tmp.exists():
            for p in tmp.iterdir():
                try:
                    if time.time() - p.stat().st_mtime > 3600:
                        p.unlink()
                except OSError:
                    pass

    def shutdown(self, backup: bool = True):
        if self.stop_event.is_set():
            return
        if getattr(self, "calls", None) is not None:
            try:
                self.calls.shutdown()   # avisa a quien esté en una llamada antes de cerrar
            except Exception:
                self.log.exception("Error al cerrar las llamadas")
        self.stop_event.set()
        if self.turn:
            self.turn.stop()
        for srv in self.servers:
            try:
                srv.shutdown()
                srv.server_close()
            except Exception:
                pass
        if self.db is not None:
            try:
                if backup and self.db.scalar("SELECT COUNT(*) FROM users"):
                    info = self.backups.create("cierre")
                    self.backups.apply_retention(int(self.db.get_setting("backup_keep")))
                    self.log.info("Copia al detener el servidor: %s", info["name"])
            except Exception:
                self.log.exception("No se pudo crear la copia al detener el servidor")
            self.db.wake_waiters()
            self.db.close()
        self.log.info("Servidor detenido")


def run_server(cfg: Config) -> None:
    app = ServerApp(cfg)
    app.start()
    (app.data_dir / "servidor.pid").write_text(str(os.getpid()), encoding="utf-8")
    try:
        while not app.stop_event.wait(1):
            pass
    except KeyboardInterrupt:
        app.shutdown()
