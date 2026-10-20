"""Mini framework HTTP sobre la biblioteca estándar (sin dependencias externas)."""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

MAX_JSON_BODY = 4 * 1024 * 1024
CHUNK = 256 * 1024


class HttpError(Exception):
    def __init__(self, status: int, code: str, message: str, extra: dict | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra or {}


def bad_request(message: str, code: str = "datos_invalidos") -> HttpError:
    return HttpError(400, code, message)


def not_found(message: str = "No se ha encontrado el elemento solicitado.") -> HttpError:
    return HttpError(404, "no_encontrado", message)


def forbidden(message: str = "Tu usuario no tiene permiso para esta acción.") -> HttpError:
    return HttpError(403, "sin_permiso", message)


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    headers: dict = field(default_factory=dict)
    file_path: Path | None = None
    delete_after: bool = False

    @staticmethod
    def json(data, status: int = 200) -> Response:
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return Response(status, body, {"Content-Type": "application/json; charset=utf-8"})

    @staticmethod
    def file(path: Path, filename: str, content_type: str, inline: bool = False,
             delete_after: bool = False) -> Response:
        from urllib.parse import quote
        disp = "inline" if inline else "attachment"
        ascii_name = re.sub(r'[^A-Za-z0-9._-]', "_", filename) or "archivo"
        headers = {
            "Content-Type": content_type,
            "Content-Disposition": f"{disp}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        }
        return Response(200, b"", headers, file_path=Path(path), delete_after=delete_after)

    @staticmethod
    def bytes(data: bytes, filename: str, content_type: str) -> Response:
        r = Response.file(Path("."), filename, content_type)
        r.file_path = None
        r.body = data
        return r


@dataclass
class Request:
    method: str
    path: str
    query: dict
    headers: dict
    client_ip: str
    body: bytes = b""
    upload_path: Path | None = None
    user: dict | None = None
    session_hash: str | None = None
    params: dict = field(default_factory=dict)

    @property
    def json(self) -> dict:
        if not self.body:
            return {}
        try:
            data = json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise bad_request("El contenido enviado no es un JSON válido.")
        if not isinstance(data, dict):
            raise bad_request("Se esperaba un objeto JSON.")
        return data

    def q(self, name: str, default: str | None = None) -> str | None:
        return self.query.get(name, default)

    def q_int(self, name: str, default: int | None = None) -> int | None:
        raw = self.query.get(name)
        if raw in (None, ""):
            return default
        try:
            return int(raw)
        except ValueError:
            raise bad_request(f"El parámetro '{name}' debe ser un número.")

    @property
    def is_loopback(self) -> bool:
        return self.client_ip in ("127.0.0.1", "::1") or self.client_ip.startswith("::ffff:127.")


@dataclass
class Route:
    method: str
    regex: re.Pattern
    handler: object
    auth: bool = True
    perm: str | None = None
    idempotent: bool = False
    upload: bool = False
    raw: bool = False


class Router:
    def __init__(self):
        self.routes: list[Route] = []

    def route(self, method: str, pattern: str, *, auth: bool = True, perm: str | None = None,
              idempotent: bool = False, upload: bool = False, raw: bool = False):
        regex = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern) + "$")

        def deco(fn):
            self.routes.append(Route(method, regex, fn, auth, perm, idempotent, upload, raw))
            return fn
        return deco

    def match(self, method: str, path: str):
        allowed = False
        for r in self.routes:
            m = r.regex.match(path)
            if m:
                if r.method == method:
                    return r, {k: unquote(v) for k, v in m.groupdict().items()}
                allowed = True
        if allowed:
            raise HttpError(405, "metodo_no_permitido", "Método no permitido.")
        raise not_found("Ruta desconocida.")


def parse_query(raw: str) -> dict:
    return {k: v[-1] for k, v in parse_qs(raw, keep_blank_values=True).items()}


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True
    # En Windows SO_REUSEADDR permitiría a otro proceso robar el puerto; solo se usa en Linux/macOS.
    allow_reuse_address = os.name != "nt"
    request_queue_size = 64

    def handle_error(self, request, client_address):  # pragma: no cover - ruido de desconexiones
        import logging
        logging.getLogger("devcontrol").debug("Conexión interrumpida con %s", client_address)


class BaseHandler(BaseHTTPRequestHandler):
    """Lee la petición, delega en `app.dispatch` y escribe la respuesta."""

    server_version = "DevControl"
    sys_version = ""
    protocol_version = "HTTP/1.0"
    app = None  # se asigna en una subclase

    def log_message(self, fmt, *args):  # silencio en consola; ver registros propios
        pass

    def _handle(self):
        split = urlsplit(self.path)
        req = Request(
            method=self.command,
            path=split.path,
            query=parse_query(split.query),
            headers={k.lower(): v for k, v in self.headers.items()},
            client_ip=self.client_address[0],
        )
        try:
            resp = self.app.dispatch(self, req)
        except HttpError as exc:
            resp = Response.json({"error": exc.code, "message": exc.message, **exc.extra}, exc.status)
        finally:
            if req.upload_path and req.upload_path.exists():
                try:
                    req.upload_path.unlink()
                except OSError:
                    pass
        self.send_resp(resp)

    def send_resp(self, resp: Response):
        try:
            self.send_response(resp.status)
            if resp.file_path is not None:
                size = resp.file_path.stat().st_size
                resp.headers["Content-Length"] = str(size)
            else:
                resp.headers["Content-Length"] = str(len(resp.body))
            for k, v in resp.headers.items():
                self.send_header(k, v)
            self.end_headers()
            if self.command == "HEAD":
                return
            if resp.file_path is not None:
                with open(resp.file_path, "rb") as fh:
                    shutil.copyfileobj(fh, self.wfile, CHUNK)
            else:
                self.wfile.write(resp.body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            if resp.file_path is not None and resp.delete_after:
                try:
                    os.unlink(resp.file_path)
                except OSError:
                    pass

    def read_body(self, req: Request, upload: bool, tmp_dir: Path, max_bytes: int) -> None:
        length_raw = self.headers.get("Content-Length")
        if length_raw is None:
            if self.command in ("POST", "PUT", "PATCH") and upload:
                raise HttpError(411, "falta_longitud", "Falta la cabecera Content-Length.")
            return
        try:
            length = int(length_raw)
        except ValueError:
            raise bad_request("Content-Length inválido.")
        if upload:
            if length > max_bytes:
                # Se descarta el cuerpo para poder responder con un error claro.
                self._discard(length)
                raise HttpError(413, "archivo_demasiado_grande",
                                f"El archivo supera el tamaño máximo permitido ({max_bytes // (1024 * 1024)} MB).")
            tmp_dir.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(dir=tmp_dir, prefix="subida-")
            req.upload_path = Path(name)
            remaining = length
            with os.fdopen(fd, "wb") as fh:
                while remaining > 0:
                    chunk = self.rfile.read(min(CHUNK, remaining))
                    if not chunk:
                        raise HttpError(400, "subida_incompleta",
                                        "La subida se ha interrumpido antes de terminar. Vuelve a intentarlo.")
                    fh.write(chunk)
                    remaining -= len(chunk)
        else:
            if length > MAX_JSON_BODY:
                self._discard(length)
                raise HttpError(413, "contenido_demasiado_grande", "El contenido enviado es demasiado grande.")
            req.body = self.rfile.read(length) if length else b""

    def _discard(self, length: int) -> None:
        remaining = length
        try:
            while remaining > 0:
                chunk = self.rfile.read(min(CHUNK, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
        except OSError:
            pass

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = _handle
