"""Programa local de DEV CONTROL (se ejecuta en cada equipo).

Solo escucha en 127.0.0.1. Sirve la interfaz, reenvía las peticiones al servidor
elegido al iniciar sesión y guarda en una cola persistente las operaciones que no
se han podido enviar por falta de conexión. Cada servidor tiene su propia cola y
su propia caché, de modo que cambiar de servidor nunca mezcla datos. Cada
operación lleva una clave de idempotencia: al reenviarla tras una reconexión el
servidor nunca la duplica.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import mimetypes
import os
import re
import secrets
import shutil
import socket
import sqlite3
import tempfile
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .. import APP_NAME, APP_VERSION
from ..config import Config, ConfigError, format_server_address, parse_server_address
from ..logs import setup_logging
from ..paths import WEB_DIR, Layout
from ..server.web import QuietServer

CHUNK = 256 * 1024
FORWARD_HEADERS = ("authorization", "content-type", "idempotency-key")
RESPONSE_HEADERS = ("content-type", "content-disposition", "x-content-type-options", "x-idempotent-replay",
                    "cache-control")
CSP = ("default-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; "
       "script-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
NO_CACHE_PATHS = ("/api/changes", "/api/attachments/", "/api/export/", "/api/backups/", "/api/vault", "/api/calls")
MAX_PROFILES = 8

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def safe_dir_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", text)[:80] or "servidor"


class Outbox:
    """Cola local persistente de operaciones pendientes de enviar (una por servidor)."""

    def __init__(self, path: Path):
        self.path = path
        self.dir = path.parent / "pendientes"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                method TEXT NOT NULL,
                path TEXT NOT NULL,
                headers TEXT NOT NULL,
                body BLOB,
                body_file TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                summary TEXT
            );
            CREATE TABLE IF NOT EXISTS cache (
                key TEXT PRIMARY KEY,
                body BLOB NOT NULL,
                content_type TEXT NOT NULL,
                saved_at TEXT NOT NULL
            );
        """)

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    def add(self, method, path, headers, body, body_file, summary) -> int:
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO outbox(created_at, method, path, headers, body, body_file, summary) VALUES (?,?,?,?,?,?,?)",
                (utc_now(), method, path, json.dumps(headers), body, body_file, json.dumps(summary, ensure_ascii=False)))
            return cur.lastrowid

    def items(self, statuses=("pending", "failed")) -> list[dict]:
        with self.lock:
            marks = ",".join("?" * len(statuses))
            rows = self.conn.execute(f"SELECT id, created_at, method, path, status, attempts, last_error, summary "
                                     f"FROM outbox WHERE status IN ({marks}) ORDER BY id", statuses).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["summary"] = json.loads(d["summary"]) if d["summary"] else {}
            out.append(d)
        return out

    def next_pending(self) -> dict | None:
        with self.lock:
            row = self.conn.execute("SELECT * FROM outbox WHERE status='pending' ORDER BY id LIMIT 1").fetchone()
            return dict(row) if row else None

    def pending_count(self) -> int:
        with self.lock:
            return self.conn.execute("SELECT COUNT(*) FROM outbox WHERE status='pending'").fetchone()[0]

    def done(self, item_id: int) -> None:
        with self.lock:
            row = self.conn.execute("SELECT body_file FROM outbox WHERE id=?", (item_id,)).fetchone()
            self.conn.execute("DELETE FROM outbox WHERE id=?", (item_id,))
        if row and row["body_file"]:
            try:
                os.unlink(row["body_file"])
            except OSError:
                pass

    def fail(self, item_id: int, error: str, final: bool) -> None:
        with self.lock:
            self.conn.execute("UPDATE outbox SET attempts=attempts+1, last_error=?, status=? WHERE id=?",
                              (error, "failed" if final else "pending", item_id))

    def retry(self, item_id: int, authorization: str | None) -> bool:
        with self.lock:
            row = self.conn.execute("SELECT headers FROM outbox WHERE id=?", (item_id,)).fetchone()
            if not row:
                return False
            headers = json.loads(row["headers"])
            if authorization:
                headers["authorization"] = authorization
            self.conn.execute("UPDATE outbox SET status='pending', last_error=NULL, headers=? WHERE id=?",
                              (json.dumps(headers), item_id))
            return True

    def cache_put(self, key: str, body: bytes, ctype: str) -> None:
        with self.lock:
            self.conn.execute("INSERT INTO cache(key, body, content_type, saved_at) VALUES (?,?,?,?) "
                              "ON CONFLICT(key) DO UPDATE SET body=excluded.body, content_type=excluded.content_type, "
                              "saved_at=excluded.saved_at", (key, body, ctype, utc_now()))

    def cache_get(self, key: str):
        with self.lock:
            row = self.conn.execute("SELECT body, content_type, saved_at FROM cache WHERE key=?", (key,)).fetchone()
            return dict(row) if row else None


class NullUpdater:
    """Sustituto cuando no hay actualizador (pruebas o ejecución desde el código fuente)."""

    def status(self) -> dict:
        return {"state": "idle", "mandatory": False, "available": False, "current": APP_VERSION, "can_apply": False}

    def check(self, force: bool = False) -> None:
        pass

    def apply(self) -> None:
        raise RuntimeError("Las actualizaciones automáticas solo están disponibles en el programa instalado.")

    def note_server(self, info: dict | None, required: dict | None = None) -> None:
        pass


class Agent:
    def __init__(self, cfg: Config, layout: Layout, web_dir: Path | None = None, updater=None):
        self.cfg = cfg
        self.layout = layout
        self.client_root = layout.client_root
        self.client_root.mkdir(parents=True, exist_ok=True)
        self.web_dir = Path(web_dir or WEB_DIR)
        self.log = setup_logging(layout.logs_dir, "cliente")
        self.tmp_dir = self.client_root / "tmp"
        self.tmp_dir.mkdir(exist_ok=True)
        self.updater = updater or NullUpdater()
        self.local_port = cfg.local_port
        self.hooks: dict = {}  # notify(title, body, urgent), open_path(path), host_status(), host_start(), host_stop()
        self.computer_name = socket.gethostname()
        self.flush_event = threading.Event()
        self.stop_event = threading.Event()
        self._flush_lock = threading.Lock()
        self._conn_lock = threading.RLock()
        self.httpd: QuietServer | None = None
        self.outbox: Outbox | None = None
        secret_file = self.client_root / ".secreto_local"
        if not secret_file.exists():
            secret_file.write_text(secrets.token_urlsafe(24), encoding="utf-8")
        self.local_secret = secret_file.read_text(encoding="utf-8").strip()
        last = self._load_json("conexion.json", {}).get("server", "")
        try:
            self.connect(last)
        except ConfigError:
            self.connect("")

    # -- servidor elegido -------------------------------------------------------
    def _load_json(self, name: str, default):
        try:
            return json.loads((self.client_root / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def _save_json(self, name: str, data) -> None:
        tmp = self.client_root / (name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.client_root / name)

    def resolve(self, server: str) -> tuple[str, int, bool]:
        """'' = servidor por defecto de este equipo. Devuelve (host, puerto, es_por_defecto)."""
        server = (server or "").strip()
        if not server:
            host, port = self.cfg.default_server_address()
            return host, port, True
        host, port = parse_server_address(server, self.cfg.server_port)
        return host, port, False

    def connect(self, server: str) -> dict:
        host, port, is_default = self.resolve(server)
        with self._conn_lock:
            key = f"{host}:{port}"
            if self.outbox is None or key != getattr(self, "server_key", None):
                old = self.outbox
                folder = self.client_root / "servidores" / safe_dir_name(key)
                folder.mkdir(parents=True, exist_ok=True)
                self.outbox = Outbox(folder / "cliente.db")
                if old is not None:
                    old.close()
                self.server_host, self.server_port, self.server_key = host, port, key
                self.reachable = False
                self.last_ok_at: str | None = None
                self.last_error = ""
                self.server_info: dict = {}
                self.update_required: dict | None = None
                self.log.info("Servidor elegido: %s", key)
            self.server_is_default = is_default
            self._save_json("conexion.json", {"server": "" if is_default else server})
        self.flush_event.set()
        self.check_health()
        return self.status()

    @property
    def is_host_server(self) -> bool:
        return self.cfg.is_host and self.server_port == self.cfg.server_port and \
            self.cfg.is_host_itself(self._ip_of(self.server_host))

    @staticmethod
    def _ip_of(host: str) -> str:
        if host in ("localhost",):
            return "127.0.0.1"
        try:
            return socket.getaddrinfo(host, None)[0][4][0]
        except OSError:
            return host

    def profiles(self) -> list[dict]:
        return self._load_json("perfiles.json", [])

    def remember_profile(self, server: str, label: str = "") -> None:
        server = server.strip()
        items = [p for p in self.profiles() if p.get("server") != server]
        items.insert(0, {"server": server, "label": label[:60], "last_used": utc_now()})
        self._save_json("perfiles.json", items[:MAX_PROFILES])

    def forget_profile(self, server: str) -> None:
        self._save_json("perfiles.json", [p for p in self.profiles() if p.get("server") != server])

    # -- estado de la conexión -------------------------------------------------
    def mark_up(self):
        if not self.reachable:
            self.log.info("Conexión con el servidor %s recuperada", self.server_key)
            self.flush_event.set()
        self.reachable = True
        self.last_ok_at = utc_now()
        self.last_error = ""

    def mark_down(self, exc: Exception):
        msg = describe_network_error(exc, self.server_host, self.server_port, self.is_host_server)
        if self.reachable or msg != self.last_error:
            self.log.warning("Servidor no disponible: %s", msg)
        self.reachable = False
        self.last_error = msg

    def status(self) -> dict:
        host_status = self.hooks["host_status"]() if "host_status" in self.hooks else None
        default_host, default_port = self.cfg.default_server_address()
        return {
            "app": APP_NAME, "version": APP_VERSION, "server": self.server_key, "server_host": self.server_host,
            "server_label": format_server_address(self.server_host, self.server_port),
            "server_is_default": self.server_is_default,
            "default_server_label": "este equipo" if self.cfg.is_host and not self.cfg.default_server
            else format_server_address(default_host, default_port),
            "reachable": self.reachable, "last_ok_at": self.last_ok_at, "last_error": self.last_error,
            "server_info": self.server_info, "outbox": self.outbox.items(), "pending_count": self.outbox.pending_count(),
            "is_host": self.cfg.is_host, "is_host_server": self.is_host_server, "host": host_status,
            "profiles": self.profiles(), "desktop": "notify" in self.hooks, "computer_name": self.computer_name,
            "config_file": str(self.cfg.path) if self.cfg.path else "", "update": self.update_status(),
        }

    def update_status(self) -> dict:
        try:
            return self.updater.status()
        except Exception as exc:  # el estado nunca debe romper la interfaz
            return {"state": "error", "error": str(exc), "mandatory": False}

    # -- comunicación con el servidor -----------------------------------------
    def send(self, method: str, path: str, headers: dict, body: bytes | None = None,
             body_file: str | None = None, timeout: float = 30, host: str | None = None, port: int | None = None):
        conn = http.client.HTTPConnection(host or self.server_host, port or self.server_port, timeout=timeout)
        conn.putrequest(method, path, skip_accept_encoding=True)
        conn.putheader("X-DevControl-Version", APP_VERSION)
        for k, v in headers.items():
            conn.putheader(k, v)
        if body_file:
            conn.putheader("Content-Length", str(os.path.getsize(body_file)))
            conn.endheaders()
            with open(body_file, "rb") as fh:
                for chunk in iter(lambda: fh.read(CHUNK), b""):
                    conn.send(chunk)
        else:
            data = body or b""
            if method in ("POST", "PUT", "PATCH", "DELETE") or data:
                conn.putheader("Content-Length", str(len(data)))
            conn.endheaders()
            if data:
                conn.send(data)
        return conn, conn.getresponse()

    def check_health(self) -> None:
        try:
            conn, resp = self.send("GET", "/api/health", {}, timeout=5)
            data = resp.read()
            conn.close()
            if resp.status == 200:
                self.server_info = json.loads(data)
                self.mark_up()
                self.updater.note_server(self.server_info, self.update_required)
            else:
                self.mark_down(OSError(f"El servidor respondió {resp.status}"))
        except (OSError, http.client.HTTPException, ValueError) as exc:
            self.mark_down(exc)

    def test_server(self, server: str) -> dict:
        try:
            host, port, _ = self.resolve(server)
        except ConfigError as exc:
            return {"ok": False, "message": str(exc)}
        label = format_server_address(host, port)
        try:
            conn, resp = self.send("GET", "/api/health", {}, timeout=5, host=host, port=port)
            raw = resp.read()
            conn.close()
        except (OSError, http.client.HTTPException) as exc:
            return {"ok": False, "server_label": label, "message": describe_network_error(exc, host, port, False)}
        try:
            info = json.loads(raw)
        except ValueError:
            info = {}
        if resp.status != 200 or info.get("app") != APP_NAME:
            msg = info.get("message") or f"En {label} hay algo que no es un servidor de DEV CONTROL (respuesta {resp.status})."
            return {"ok": False, "server_label": label, "message": msg}
        return {"ok": True, "server_label": label, "version": info.get("version"), "info": info}

    def health_loop(self):
        while not self.stop_event.is_set():
            self.check_health()
            self.stop_event.wait(5 if self.reachable else 3)

    def flush_loop(self):
        while not self.stop_event.is_set():
            self.flush_event.wait(3)
            self.flush_event.clear()
            if self.stop_event.is_set():
                break
            self.flush()

    def flush(self) -> int:
        """Envía en orden las operaciones pendientes del servidor actual. Devuelve cuántas se enviaron."""
        sent = 0
        with self._flush_lock:
            outbox = self.outbox
            while True:
                item = outbox.next_pending()
                if not item or outbox is not self.outbox:
                    return sent
                headers = json.loads(item["headers"])
                try:
                    conn, resp = self.send(item["method"], item["path"], headers, item["body"], item["body_file"],
                                           timeout=600 if item["body_file"] else 30)
                    raw = resp.read()
                    conn.close()
                except (OSError, http.client.HTTPException) as exc:
                    self.mark_down(exc)
                    outbox.fail(item["id"], self.last_error, final=False)
                    return sent
                self.mark_up()
                if resp.status < 300:
                    outbox.done(item["id"])
                    sent += 1
                    self.log.info("Operación pendiente %s %s enviada (%s)", item["method"], item["path"], resp.status)
                    continue
                try:
                    message = json.loads(raw).get("message") or f"Error {resp.status}"
                except (ValueError, AttributeError):
                    message = f"Error {resp.status}"
                if resp.status >= 500 or resp.status in (408, 426, 429):
                    outbox.fail(item["id"], message, final=item["attempts"] >= 9)
                    return sent
                self.log.warning("Operación pendiente %s %s rechazada: %s", item["method"], item["path"], message)
                outbox.fail(item["id"], message, final=True)

    def save_from_server(self, path: str, authorization: str, folder: str, open_after: bool) -> dict:
        """Descarga un archivo del servidor y lo guarda en Exportaciones o Descargas."""
        if not path.startswith("/api/"):
            raise ValueError("Ruta no válida.")
        headers = {"authorization": authorization} if authorization else {}
        conn, resp = self.send("GET", path, headers, timeout=600)
        try:
            if resp.status != 200:
                raw = resp.read()
                try:
                    message = json.loads(raw).get("message")
                except ValueError:
                    message = None
                raise ValueError(message or f"El servidor respondió {resp.status}.")
            disp = resp.getheader("Content-Disposition", "")
            m = re.search(r"filename\*=UTF-8''([^;]+)", disp) or re.search(r'filename="([^"]+)"', disp)
            name = unquote(m.group(1)) if m else Path(urlsplit(path).path).name or "archivo"
            name = re.sub(r'[\\/:*?"<>|]', "_", name).strip(" .") or "archivo"
            if folder == "exportaciones":
                target_dir = self.layout.exports_dir
            else:
                target_dir = Path.home() / "Downloads"
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / name
            stem, suffix, n = target.stem, target.suffix, 2
            while target.exists():
                target = target_dir / f"{stem} ({n}){suffix}"
                n += 1
            partial = target.with_name(target.name + ".parcial")
            with open(partial, "wb") as fh:
                while True:
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    fh.write(chunk)
            os.replace(partial, target)
        finally:
            conn.close()
        if open_after and "open_path" in self.hooks:
            try:
                self.hooks["open_path"](target)
            except OSError as exc:  # el archivo está guardado aunque no se pueda abrir
                self.log.warning("No se pudo abrir %s: %s", target, exc)
        return {"ok": True, "file": str(target), "folder": str(target_dir), "name": target.name}

    # -- arranque ----------------------------------------------------------------
    def start(self, port: int | None = None) -> int:
        agent = self

        class Handler(AgentHandler):
            pass
        Handler.agent = agent
        self.httpd = QuietServer(("127.0.0.1", self.local_port if port is None else port), Handler)
        self.local_port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, name="cliente-http", daemon=True).start()
        threading.Thread(target=self.health_loop, name="salud", daemon=True).start()
        threading.Thread(target=self.flush_loop, name="cola", daemon=True).start()
        self.log.info("Programa local en http://127.0.0.1:%s -> servidor %s", self.local_port, self.server_key)
        return self.local_port

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.local_port}/"

    def shutdown(self):
        self.stop_event.set()
        self.flush_event.set()
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()


def describe_network_error(exc: Exception, host: str, port: int, own: bool) -> str:
    if isinstance(exc, ConnectionRefusedError):
        if own:
            return "El servidor de este equipo está detenido. Inícialo desde el icono de DEV CONTROL en la bandeja."
        return (f"El equipo {host} responde, pero DEV CONTROL no está en marcha en el puerto {port}. "
                "Pide que se abra DEV CONTROL en el equipo anfitrión.")
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return (f"No hay respuesta de {host}. El equipo anfitrión puede estar apagado o sin conexión, o la VPN "
                "(por ejemplo Tailscale) desconectada.")
    if isinstance(exc, socket.gaierror):
        return f"No se encuentra el equipo «{host}». Revisa la dirección del servidor."
    if isinstance(exc, OSError) and getattr(exc, "errno", None) in (101, 113, 10051, 10065):
        return f"No hay ruta hasta {host}. Comprueba la conexión de red o la VPN de este equipo."
    return f"No se puede conectar con {host}:{port} ({exc.__class__.__name__}: {exc})."


class AgentHandler(BaseHTTPRequestHandler):
    agent: Agent = None
    server_version = "DevControl-Local"
    sys_version = ""
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt, *args):
        pass

    # -- utilidades ---------------------------------------------------------------
    def _json(self, status: int, data: dict, extra_headers: dict | None = None):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra_headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        port = self.agent.local_port
        return host in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _read_body(self, to_file: bool):
        length = int(self.headers.get("Content-Length") or 0)
        if not to_file:
            return (self.rfile.read(length) if length else b""), None
        fd, name = tempfile.mkstemp(dir=self.agent.tmp_dir, prefix="subida-")
        remaining = length
        with os.fdopen(fd, "wb") as fh:
            while remaining > 0:
                chunk = self.rfile.read(min(CHUNK, remaining))
                if not chunk:
                    break
                fh.write(chunk)
                remaining -= len(chunk)
        if remaining:
            os.unlink(name)
            raise ConnectionAbortedError("subida incompleta")
        return None, name

    def _json_body(self) -> dict:
        body, _ = self._read_body(False)
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            data = {}
        return data if isinstance(data, dict) else {}

    # -- despacho ---------------------------------------------------------------------
    def _handle(self):
        if not self._host_ok():
            return self._json(403, {"error": "host_no_valido", "message": "Acceso no permitido."})
        path = urlsplit(self.path).path
        try:
            if path.startswith("/api/"):
                return self._proxy()
            if path.startswith("/local/"):
                return self._local(path)
            if self.command in ("GET", "HEAD"):
                return self._static(path)
            return self._json(404, {"error": "no_encontrado", "message": "Ruta desconocida."})
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception:
            self.agent.log.exception("Error en el programa local (%s %s)", self.command, path)
            self._json(500, {"error": "error_cliente", "message": "Error interno del programa local. El detalle está en "
                                                                  "el registro cliente.log."})

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = _handle

    def _static(self, path: str):
        if path in ("/", ""):
            path = "/index.html"
        web = self.agent.web_dir.resolve()
        target = (web / unquote(path).lstrip("/")).resolve()
        if web not in target.parents or not target.is_file():
            return self._json(404, {"error": "no_encontrado", "message": "Archivo no encontrado."})
        data = target.read_bytes()
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        if target.suffix == ".html":
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _local(self, path: str):
        agent = self.agent
        method = self.command
        # Las páginas web ajenas no pueden enviar JSON a 127.0.0.1 sin una consulta previa que aquí no se acepta.
        if method in ("POST", "PUT", "DELETE") and path not in ("/local/show", "/local/shutdown") and \
                not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self._json(415, {"error": "tipo_no_valido", "message": "Petición no válida."})
        if path == "/local/status" and method == "GET":
            return self._json(200, agent.status())
        if path == "/local/connect" and method == "POST":
            try:
                return self._json(200, agent.connect(str(self._json_body().get("server") or "")))
            except ConfigError as exc:
                return self._json(400, {"error": "servidor_no_valido", "message": str(exc)})
        if path == "/local/test" and method == "POST":
            return self._json(200, agent.test_server(str(self._json_body().get("server") or "")))
        if path == "/local/profiles" and method == "POST":
            data = self._json_body()
            server = "" if agent.server_is_default else str(data.get("server") or "")
            if server:
                agent.remember_profile(server, str(data.get("label") or ""))
            return self._json(200, {"profiles": agent.profiles()})
        if path == "/local/profiles/forget" and method == "POST":
            agent.forget_profile(str(self._json_body().get("server") or ""))
            return self._json(200, {"profiles": agent.profiles()})
        if path == "/local/flush" and method == "POST":
            agent.flush_event.set()
            return self._json(200, {"ok": True})
        if path.startswith("/local/outbox/"):
            try:
                item_id = int(path.split("/")[3])
            except (IndexError, ValueError):
                return self._json(400, {"error": "id", "message": "Identificador no válido."})
            if method == "DELETE":
                agent.outbox.done(item_id)
                return self._json(200, {"ok": True})
            if method == "POST" and path.endswith("/retry"):
                ok = agent.outbox.retry(item_id, self.headers.get("Authorization"))
                agent.flush_event.set()
                return self._json(200 if ok else 404, {"ok": ok})
        if path == "/local/save" and method == "POST":
            data = self._json_body()
            try:
                result = agent.save_from_server(str(data.get("path") or ""), self.headers.get("Authorization", ""),
                                                str(data.get("folder") or "descargas"), bool(data.get("open", True)))
            except (OSError, http.client.HTTPException) as exc:
                return self._json(503, {"error": "servidor_no_disponible",
                                        "message": describe_network_error(exc, agent.server_host, agent.server_port,
                                                                          agent.is_host_server)})
            except ValueError as exc:
                return self._json(400, {"error": "descarga", "message": str(exc)})
            return self._json(200, result)
        if path == "/local/open" and method == "POST":
            what = str(self._json_body().get("what") or "")
            places = {"copias": agent.layout.backups_dir, "exportaciones": agent.layout.exports_dir,
                      "actualizaciones": agent.layout.updates_dir, "configuracion": agent.layout.config_dir,
                      "registros": agent.layout.logs_dir, "datos": agent.layout.docs_root}
            if what not in places or "open_path" not in agent.hooks:
                return self._json(400, {"error": "no_disponible", "message": "No disponible en este equipo."})
            agent.hooks["open_path"](places[what])
            return self._json(200, {"ok": True, "path": str(places[what])})
        if path == "/local/attention" and method == "POST":
            # Llamada entrante: aviso de Windows y ventana delante aunque estuviera oculta.
            data = self._json_body()
            if "notify" in agent.hooks:
                agent.hooks["notify"](str(data.get("title") or APP_NAME)[:120], str(data.get("body") or "")[:300], True)
            if data.get("show") and "show" in agent.hooks:
                threading.Thread(target=agent.hooks["show"], daemon=True).start()
            return self._json(200, {"ok": True})
        if path == "/local/notify" and method == "POST":
            data = self._json_body()
            if "notify" in agent.hooks:
                agent.hooks["notify"](str(data.get("title") or APP_NAME)[:120], str(data.get("body") or "")[:300],
                                      bool(data.get("urgent")))
            return self._json(200, {"ok": True})
        if path == "/local/update" and method == "GET":
            return self._json(200, agent.update_status())
        if path == "/local/update/check" and method == "POST":
            threading.Thread(target=agent.updater.check, kwargs={"force": True}, daemon=True).start()
            return self._json(200, agent.update_status())
        if path == "/local/update/apply" and method == "POST":
            try:
                agent.updater.apply()
            except RuntimeError as exc:
                return self._json(400, {"error": "actualizacion", "message": str(exc)})
            return self._json(200, agent.update_status())
        if path == "/local/update/ack" and method == "POST":
            if hasattr(agent.updater, "ack"):
                agent.updater.ack()
            return self._json(200, agent.update_status())
        if path in ("/local/server/start", "/local/server/stop") and method == "POST":
            hook = agent.hooks.get("host_start" if path.endswith("start") else "host_stop")
            if not hook:
                return self._json(400, {"error": "no_anfitrion", "message": "Este equipo no es el anfitrión."})
            try:
                hook()
            except Exception as exc:
                return self._json(500, {"error": "servidor", "message": str(exc)})
            return self._json(200, agent.status())
        if path in ("/local/shutdown", "/local/show") and method == "POST":
            if self.headers.get("X-DevControl-Local-Secret") != agent.local_secret:
                return self._json(403, {"error": "sin_permiso", "message": "No autorizado."})
            hook = agent.hooks.get("show" if path.endswith("show") else "quit")
            if hook:
                threading.Thread(target=hook, daemon=True).start()
            elif path.endswith("shutdown"):
                threading.Thread(target=agent.shutdown, daemon=True).start()
            return self._json(200, {"ok": True})
        return self._json(404, {"error": "no_encontrado", "message": "Ruta local desconocida."})

    def _proxy(self):
        agent = self.agent
        method = self.command
        full_path = self.path
        path = urlsplit(full_path).path
        is_upload = path == "/api/uploads"
        body, body_file = self._read_body(is_upload and method == "POST")
        headers = {k: self.headers[k] for k in self.headers.keys() if k.lower() in FORWARD_HEADERS}
        queueable = self.headers.get("X-Offline-Queue") == "1" and method in ("POST", "PUT", "PATCH", "DELETE") \
            and "idempotency-key" in {k.lower() for k in headers}
        outbox = agent.outbox
        try:
            # Si ya hay operaciones en cola, las nuevas se ponen detrás para conservar el orden.
            if queueable and outbox.pending_count():
                return self._enqueue(outbox, method, full_path, headers, body, body_file, queued_behind=True)
            timeout = 30
            if path == "/api/changes":
                timeout = 45
            elif is_upload or path.startswith(("/api/backups", "/api/export")):
                timeout = 600
            try:
                conn, resp = agent.send(method, full_path, headers, body, body_file, timeout=timeout)
            except (OSError, http.client.HTTPException) as exc:
                agent.mark_down(exc)
                if queueable:
                    return self._enqueue(outbox, method, full_path, headers, body, body_file)
                if method == "GET":
                    cached = outbox.cache_get(self._cache_key(full_path))
                    if cached:
                        return self._send_raw(200, cached["body"], cached["content_type"],
                                              {"X-DevControl-Offline": "1", "X-DevControl-Cached-At": cached["saved_at"]})
                return self._json(503, {"error": "servidor_no_disponible", "message": agent.last_error})
            agent.mark_up()
            try:
                self._relay(outbox, resp, method, full_path, path)
            finally:
                conn.close()
        finally:
            if body_file and os.path.exists(body_file) and not getattr(self, "_kept_file", False):
                os.unlink(body_file)

    def _cache_key(self, full_path: str) -> str:
        auth = self.headers.get("Authorization", "")
        return hashlib.sha256(auth.encode()).hexdigest()[:16] + " " + full_path

    def _enqueue(self, outbox, method, full_path, headers, body, body_file, queued_behind=False):
        agent = self.agent
        kept = None
        if body_file:
            kept = str(outbox.dir / Path(body_file).name)
            shutil.move(body_file, kept)
            self._kept_file = True
        summary = {}
        try:
            if body:
                data = json.loads(body)
                summary = {k: data.get(k) for k in ("uid", "body", "title", "status", "channel_id", "reply_to_id",
                                                    "priority", "category", "entity_type") if k in data}
        except (ValueError, AttributeError):
            pass
        if kept:
            q = {k: v[-1] for k, v in parse_qs(urlsplit(full_path).query).items()}
            summary = {"filename": q.get("filename"), "owner_type": q.get("owner_type"),
                       "owner_uid": q.get("owner_uid"), "owner_id": q.get("owner_id"), "size": os.path.getsize(kept)}
        item_id = outbox.add(method, full_path, headers, body, kept, summary)
        agent.log.info("Operación %s %s guardada en la cola local (id %s)", method, urlsplit(full_path).path, item_id)
        agent.flush_event.set()
        message = ("Hay envíos anteriores pendientes; esta operación se enviará justo después."
                   if queued_behind and agent.reachable else
                   "Sin conexión con el servidor. Se ha guardado y se enviará automáticamente al reconectar.")
        return self._json(202, {"queued": True, "outbox_id": item_id, "message": message})

    def _send_raw(self, status, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _relay(self, outbox, resp, method, full_path, path):
        ctype = resp.getheader("Content-Type", "")
        length = resp.getheader("Content-Length")
        if resp.status == 426:
            data = resp.read()
            try:
                self.agent.update_required = json.loads(data)
            except ValueError:
                self.agent.update_required = {"min_version": None}
            self.agent.updater.note_server(self.agent.server_info, self.agent.update_required)
            return self._send_raw(426, data, ctype or "application/json; charset=utf-8")
        cacheable = (method == "GET" and resp.status == 200 and ctype.startswith("application/json")
                     and not path.startswith(NO_CACHE_PATHS) and length and int(length) < 3_000_000)
        if cacheable:
            data = resp.read()
            outbox.cache_put(self._cache_key(full_path), data, ctype)
            headers = {k: resp.getheader(k) for k in RESPONSE_HEADERS if resp.getheader(k)}
            headers.pop("content-type", None)
            return self._send_raw(resp.status, data, ctype, headers)
        self.send_response(resp.status)
        for k in RESPONSE_HEADERS:
            v = resp.getheader(k)
            if v:
                self.send_header(k, v)
        if length:
            self.send_header("Content-Length", length)
        self.end_headers()
        if method == "HEAD":
            return
        while True:
            chunk = resp.read(CHUNK)
            if not chunk:
                break
            self.wfile.write(chunk)
