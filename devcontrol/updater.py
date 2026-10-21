"""Actualizaciones obligatorias desde GitHub Releases.

Cada versión publicada incluye:
  DEVCONTROL-Setup-<versión>.exe   instalador
  manifest.json                    versión, notas, nombre, tamaño y SHA-256 del instalador
  manifest.json.sig                firma Ed25519 del manifiesto (clave privada solo en GitHub Actions)

Reglas:
  * El programa solo instala lo que está firmado con la clave del proyecto y cuyo SHA-256 coincide.
  * Anfitrión: si hay una versión nueva, la actualización es obligatoria.
  * Miembros: es obligatoria cuando el anfitrión ya tiene una versión más nueva (el anfitrión se
    actualiza primero), cuando el servidor exige una versión mínima (HTTP 426) o cuando el
    manifiesto marca una versión mínima de seguridad.
  * Sin conexión con GitHub nunca se bloquea el trabajo.
  * Antes de instalar se hace una copia de seguridad; después se comprueba que la nueva versión
    arranca y que los datos están completos. Si algo falla se vuelve a la versión anterior y a los
    datos de la copia. Todo queda escrito en Documentos\\DEV CONTROL\\Actualizaciones\\registro.txt.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from . import APP_NAME, APP_VERSION, parse_version
from .paths import Layout, program_dir

PUBLIC_KEY_B64 = "fxiG+Q9SQnmFdg5Sp6RRHuvMcA7KZWHUf0GtRsUrOEg="
MANIFEST = "manifest.json"
SIGNATURE = "manifest.json.sig"
MARKER = "pendiente.json"
LOG_NAME = "registro.txt"
STATE_LABELS = {
    "idle": "", "checking": "Buscando actualizaciones…", "downloading": "Descargando la actualización…",
    "verifying": "Comprobando la descarga…", "preparing": "Haciendo una copia de seguridad…",
    "applying": "Instalando. DEV CONTROL se cerrará y volverá a abrirse solo.", "error": "Error",
}
TABLES_TO_CHECK = ("users", "projects", "incidents", "tasks", "messages", "attachments", "activity")


class UpdateError(Exception):
    pass


def is_newer(a: str | None, b: str | None) -> bool:
    """True si la versión a es más nueva que b."""
    if not a:
        return False
    return parse_version(a) > parse_version(b or "0.0.0")


def verify_manifest(raw: bytes, signature: bytes | str, public_key_b64: str | None = None) -> dict:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        sig = base64.b64decode(signature.strip() if isinstance(signature, (bytes, str)) else b"")
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64 or PUBLIC_KEY_B64)).verify(sig, raw)
    except (InvalidSignature, ValueError) as exc:
        raise UpdateError("La firma de la actualización no es válida. No se instalará.") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise UpdateError("El manifiesto de la actualización está dañado.") from exc
    inst = data.get("installer") or {}
    if data.get("app") != APP_NAME or not data.get("version") or not inst.get("url") or not inst.get("sha256"):
        raise UpdateError("El manifiesto de la actualización está incompleto.")
    parse_version(data["version"])
    return data


def sign_manifest(raw: bytes, private_key_b64: str) -> str:
    """Usado por la publicación (GitHub Actions)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_key_b64))
    return base64.b64encode(key.sign(raw)).decode()


def write_log(layout: Layout, text: str) -> None:
    layout.updates_dir.mkdir(parents=True, exist_ok=True)
    with open(layout.updates_dir / LOG_NAME, "a", encoding="utf-8") as fh:
        fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {text}\n")


def read_marker(layout: Layout) -> dict | None:
    try:
        return json.loads((layout.updates_dir / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_marker(layout: Layout, data: dict) -> None:
    layout.updates_dir.mkdir(parents=True, exist_ok=True)
    tmp = layout.updates_dir / (MARKER + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, layout.updates_dir / MARKER)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def open_url(url: str, headers: dict | None = None, timeout: float = 20):
    """Abre una URL siguiendo redirecciones a mano: la clave de GitHub nunca se envía a otro servidor
    (las descargas de GitHub redirigen a su almacenamiento, que rechaza peticiones con esa cabecera)."""
    headers = {"User-Agent": f"DEV-CONTROL/{APP_VERSION}", **(headers or {})}
    origin = urllib.parse.urlsplit(url).netloc
    for _ in range(6):
        req = urllib.request.Request(url, headers=headers if urllib.parse.urlsplit(url).netloc == origin else
                                     {k: v for k, v in headers.items() if k.lower() != "authorization"})
        try:
            return _opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308) and exc.headers.get("Location"):
                url = urllib.parse.urljoin(url, exc.headers["Location"])
                continue
            raise
    raise urllib.error.URLError("Demasiadas redirecciones")


def _http_get(url: str, headers: dict | None = None, timeout: float = 20) -> bytes:
    with open_url(url, headers, timeout) as resp:
        return resp.read()


class Updater:
    def __init__(self, layout: Layout, cfg, hooks: dict | None = None, http_get=_http_get):
        self.layout = layout
        self.cfg = cfg
        self.hooks = hooks or {}   # prepare_update() -> dict (anfitrión), quit()
        self.http_get = http_get
        self.lock = threading.RLock()
        self.state = "idle"
        self.progress: float | None = None
        self.error = ""
        self.latest: dict | None = None
        self.last_check = 0.0
        self.server_version: str | None = None
        self.required: dict | None = None
        self.stop_event = threading.Event()
        self.source: str | None = None           # "github" o "anfitrion"
        self.installer_url: str | None = None
        self.last_server_check = 0.0

    # -- clave de acceso a GitHub (repositorio privado) -----------------------------------------
    @property
    def token_file(self) -> Path:
        return self.layout.local_root / "github-token.bin"

    def get_token(self) -> str:
        try:
            from .winutil import unprotect
            return unprotect(self.token_file.read_bytes()).decode("utf-8").strip()
        except (OSError, ValueError, UnicodeDecodeError):
            return ""

    def set_token(self, token: str) -> None:
        token = (token or "").strip()
        if not token:
            self.token_file.unlink(missing_ok=True)
            return
        if len(token) > 400 or any(ch.isspace() for ch in token):
            raise ValueError("La clave de acceso no es válida.")
        from .winutil import protect
        self.layout.local_root.mkdir(parents=True, exist_ok=True)
        tmp = self.token_file.with_suffix(".tmp")
        tmp.write_bytes(protect(token.encode("utf-8")))
        os.replace(tmp, self.token_file)

    def _gh_headers(self, accept: str = "application/vnd.github+json") -> dict:
        headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
        token = self.get_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def server_url(self) -> str | None:
        fn = self.hooks.get("server_url")
        return fn() if fn else None

    # -- estado ---------------------------------------------------------------
    @property
    def repo(self) -> str:
        return self.cfg.update_repo

    @property
    def manual_url(self) -> str:
        return f"https://github.com/{self.repo}/releases/latest"

    def note_server(self, info: dict | None, required: dict | None = None) -> None:
        with self.lock:
            self.server_version = (info or {}).get("version") or self.server_version
            if required is not None:
                self.required = required
            known = (self.latest or {}).get("version") or APP_VERSION
            # El anfitrión ya tiene una versión más nueva: se pide enseguida (sin esperar a la comprobación periódica).
            need = (is_newer(self.server_version, known) or self.required) and not self.cfg.is_host
            busy = self.state in ("checking", "downloading", "verifying", "preparing", "applying")
            if need and not busy and time.time() - self.last_server_check > 60:
                self.last_server_check = time.time()
                threading.Thread(target=self.check, kwargs={"force": True}, daemon=True).start()

    def _mandatory(self) -> tuple[bool, str]:
        latest = self.latest or {}
        if self.required and is_newer(self.required.get("min_version") or "999.0.0", APP_VERSION):
            return True, (f"El servidor exige DEV CONTROL {self.required.get('min_version')} o posterior. "
                          "Actualiza para seguir trabajando.")
        if latest.get("min_version") and is_newer(latest["min_version"], APP_VERSION):
            return True, "Esta versión ya no es segura. Es obligatorio actualizar."
        if self.cfg.is_host and is_newer(latest.get("version"), APP_VERSION):
            return True, ("Hay una versión nueva de DEV CONTROL. Este equipo es el anfitrión y se actualiza primero; "
                          "después se actualizará el resto del equipo.")
        if not self.cfg.is_host and is_newer(self.server_version, APP_VERSION):
            return True, (f"El anfitrión ya usa DEV CONTROL {self.server_version}. Actualiza este equipo para seguir "
                          "trabajando con el resto.")
        return False, ""

    def status(self) -> dict:
        with self.lock:
            mandatory, reason = self._mandatory()
            latest = self.latest or {}
            target_ok = is_newer(latest.get("version"), APP_VERSION) and (
                not self.server_version or self.cfg.is_host or not is_newer(self.server_version, latest.get("version")))
            marker = read_marker(self.layout)
            return {
                "state": self.state, "state_label": STATE_LABELS.get(self.state, ""), "progress": self.progress,
                "error": self.error, "current": APP_VERSION, "latest": latest.get("version"),
                "required": (self.required or {}).get("min_version") or self.server_version,
                "available": is_newer(latest.get("version"), APP_VERSION), "mandatory": mandatory, "reason": reason,
                "notes": latest.get("notes", ""), "published_at": latest.get("published_at"),
                "can_apply": bool(target_ok and getattr(sys, "frozen", False) and os.name == "nt"),
                "installed": bool(getattr(sys, "frozen", False)), "manual_url": self.manual_url,
                "last_check": datetime.fromtimestamp(self.last_check).isoformat() if self.last_check else None,
                "last_update": marker if marker and marker.get("result") and not marker.get("shown") else None,
                "source": self.source or ("github" if self.cfg.is_host else "anfitrion"),
                "token_configured": bool(self.get_token()), "is_host": bool(self.cfg.is_host),
                "log_tail": self.log_tail(),
            }

    def ack(self) -> None:
        acknowledge_last_update(self.layout)

    def log_tail(self, lines: int = 15) -> list[str]:
        try:
            return (self.layout.updates_dir / LOG_NAME).read_text(encoding="utf-8").splitlines()[-lines:]
        except OSError:
            return []

    # -- comprobar ----------------------------------------------------------------
    def check(self, force: bool = False) -> dict | None:
        """Anfitrión: consulta GitHub (con la clave de acceso si el repositorio es privado).
        Miembros: piden la versión al anfitrión, que se la sirve por la red privada."""
        with self.lock:
            if self.state in ("downloading", "verifying", "preparing", "applying"):
                return self.latest
            if not force and time.time() - self.last_check < self.cfg.update_every_hours * 3600:
                return self.latest
            self.state, self.error = "checking", ""
        try:
            if self.cfg.is_host or not self.server_url():
                manifest, raw, sig, url, source = self._check_github()
            else:
                manifest, raw, sig, url, source = self._check_server()
            with self.lock:
                if manifest is not None:
                    self.latest, self.installer_url, self.source = manifest, url, source
                self.state = "idle"
            if manifest is not None and source == "github":
                self._store_for_members(raw, sig)
        except urllib.error.HTTPError as exc:
            with self.lock:
                self.state, self.error = "idle", self._http_error(exc)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            with self.lock:
                self.state, self.error = "idle", f"No se puede comprobar si hay actualizaciones ({exc})."
        except UpdateError as exc:
            with self.lock:
                self.state, self.error = "error", str(exc)
        finally:
            self.last_check = time.time()
        return self.latest

    def _http_error(self, exc) -> str:
        if exc.code in (401, 403):
            return ("GitHub ha rechazado la clave de acceso (caducada o sin permiso de lectura). Pon una nueva en "
                    "Ajustes → Servidor y actualizaciones.")
        if exc.code == 404:
            if self.cfg.is_host and not self.get_token():
                return ("No se ven versiones publicadas. Si el repositorio de GitHub es privado, añade una clave de "
                        "acceso de solo lectura en Ajustes → Servidor y actualizaciones.")
            return ""   # aún no hay versiones publicadas
        return f"GitHub respondió {exc.code}."

    def _check_github(self):
        rel = json.loads(self.http_get(f"https://api.github.com/repos/{self.repo}/releases/latest",
                                       self._gh_headers()))
        assets = {a.get("name"): a.get("url") for a in rel.get("assets", [])}
        if MANIFEST not in assets or SIGNATURE not in assets:
            raise UpdateError("La última versión publicada no tiene manifiesto firmado.")
        raw = self.http_get(assets[MANIFEST], self._gh_headers("application/octet-stream"))
        sig = self.http_get(assets[SIGNATURE], self._gh_headers("application/octet-stream"))
        manifest = verify_manifest(raw, sig)
        name = manifest["installer"].get("name")
        if name not in assets:
            raise UpdateError("La versión publicada no incluye el instalador.")
        return manifest, raw, sig, assets[name], "github"

    def _check_server(self):
        base = self.server_url()
        try:
            data = json.loads(self.http_get(f"{base}/api/update/manifest", {}))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:   # el anfitrión aún no conoce ninguna versión
                return None, b"", b"", None, "anfitrion"
            raise
        raw = base64.b64decode(data["manifest"])
        sig = data["signature"].encode()
        manifest = verify_manifest(raw, sig)
        return manifest, raw, sig, f"{base}/api/update/installer", "anfitrion"

    def _store_for_members(self, raw: bytes, sig) -> None:
        """El anfitrión guarda la versión firmada y descarga el instalador para servírselo a los demás equipos."""
        self.layout.updates_dir.mkdir(parents=True, exist_ok=True)
        (self.layout.updates_dir / MANIFEST).write_bytes(raw)
        (self.layout.updates_dir / SIGNATURE).write_bytes(sig if isinstance(sig, bytes) else sig.encode())
        if is_newer(self.latest.get("version"), APP_VERSION) and self.hooks.get("predownload", True):
            threading.Thread(target=self._predownload, daemon=True).start()

    def _predownload(self) -> None:
        try:
            self._download(self.latest["installer"], quiet=True)
        except Exception as exc:
            write_log(self.layout, f"No se pudo descargar por adelantado la versión {self.latest.get('version')}: {exc}")

    def loop(self) -> None:
        self.stop_event.wait(5)
        while not self.stop_event.is_set():
            self.check()
            self.stop_event.wait(600)

    # -- aplicar ---------------------------------------------------------------------
    def apply(self) -> None:
        status = self.status()
        if not status["installed"] or os.name != "nt":
            raise RuntimeError("Las actualizaciones automáticas solo funcionan en el programa instalado en Windows. "
                               f"Descarga la versión nueva desde {self.manual_url}")
        if not status["can_apply"]:
            raise RuntimeError("Todavía no hay una versión nueva que se pueda instalar. Comprueba la conexión a Internet.")
        with self.lock:
            if self.state in ("downloading", "verifying", "preparing", "applying"):
                return
            self.state, self.error, self.progress = "downloading", "", 0.0
        threading.Thread(target=self._apply, name="actualizar", daemon=True).start()

    def _download(self, inst: dict, quiet: bool = False) -> Path:
        name = Path(inst.get("name") or inst["url"].rsplit("/", 1)[-1]).name
        target = self.layout.updates_dir / name
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == inst["sha256"].lower():
            return target
        partial = target.with_name(target.name + (".previa" if quiet else ".parcial"))
        if self.source == "anfitrion":
            url, headers = self.installer_url, {}
        elif self.installer_url:
            url, headers = self.installer_url, self._gh_headers("application/octet-stream")
        else:
            url, headers = inst["url"], {}
        digest = hashlib.sha256()
        try:
            resp_cm = open_url(url, headers, timeout=60)
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and self.source == "anfitrion":
                raise UpdateError("El anfitrión todavía no tiene el instalador de la versión nueva. Se podrá instalar "
                                  "en cuanto el anfitrión la descargue (lo hace solo al detectarla).") from exc
            raise
        with resp_cm as resp, open(partial, "wb") as fh:
            total = int(resp.headers.get("Content-Length") or inst.get("size") or 0)
            got = 0
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
                digest.update(chunk)
                got += len(chunk)
                if total and not quiet:
                    self.progress = min(1.0, got / total)
        if not quiet:
            self.state = "verifying"
        if digest.hexdigest() != inst["sha256"].lower() or (inst.get("size") and got != int(inst["size"])):
            partial.unlink(missing_ok=True)
            raise UpdateError("La descarga no coincide con la versión publicada (puede estar dañada). Vuelve a intentarlo.")
        os.replace(partial, target)
        return target

    def _apply(self) -> None:
        latest = self.latest or {}
        try:
            self.layout.updates_dir.mkdir(parents=True, exist_ok=True)
            installer = self._download(latest["installer"])
            self.state = "preparing"
            marker = {"from": APP_VERSION, "to": latest["version"], "started_at": datetime.now().isoformat(),
                      "host": bool(self.cfg.is_host), "installer": str(installer)}
            if "prepare_update" in self.hooks:
                marker.update(self.hooks["prepare_update"]() or {})
            prog = program_dir()
            previous = self.layout.local_root / "Programa anterior"
            if previous.exists():
                shutil.rmtree(previous, ignore_errors=True)
            shutil.copytree(prog, previous)
            marker["program_dir"] = str(prog)
            marker["previous_dir"] = str(previous)
            write_marker(self.layout, marker)
            write_log(self.layout, f"Iniciando la actualización de {APP_VERSION} a {latest['version']}.")
            script = self.layout.updates_dir / "aplicar_actualizacion.ps1"
            script.write_text(build_script(installer, prog, previous, self.layout, os.getpid()), encoding="utf-8-sig")
            self.state = "applying"
            flags = 0x08000000 | 0x00000008  # CREATE_NO_WINDOW | DETACHED_PROCESS
            subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                              "-File", str(script)], creationflags=flags, close_fds=True)
            time.sleep(1)
            if "quit" in self.hooks:
                self.hooks["quit"]()
        except Exception as exc:  # cualquier fallo deja el programa como estaba
            self.state, self.error, self.progress = "error", str(exc), None
            write_log(self.layout, f"No se pudo actualizar a {latest.get('version')}: {exc}")


def _ps(text) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def build_script(installer: Path, prog: Path, previous: Path, layout: Layout, pid: int) -> str:
    exe = prog / "DEV CONTROL.exe"
    log = layout.updates_dir / LOG_NAME
    inst_log = layout.updates_dir / "instalador.log"
    return f"""$ErrorActionPreference = 'Continue'
$log = {_ps(log)}
$exe = {_ps(exe)}
$prog = {_ps(prog)}
$prev = {_ps(previous)}
function Write-Log($t) {{ Add-Content -LiteralPath $log -Value ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + '  ' + $t) -Encoding UTF8 }}
function Restore-Previous($why) {{
  Write-Log ('FALLO: ' + $why + ' Se vuelve a la versión anterior.')
  robocopy $prev $prog /MIR /R:2 /W:2 /NFL /NDL /NJH /NJS | Out-Null
  $r = Start-Process -FilePath $exe -ArgumentList '--revertir-datos' -Wait -PassThru
  Start-Process -FilePath $exe
  exit 1
}}
$deadline = (Get-Date).AddSeconds(90)
while ((Get-Process -Id {pid} -ErrorAction SilentlyContinue) -and ((Get-Date) -lt $deadline)) {{ Start-Sleep -Milliseconds 500 }}
Start-Sleep -Seconds 1
$p = Start-Process -FilePath {_ps(installer)} -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CLOSEAPPLICATIONS', '/ACTUALIZACION=1', ('/LOG="' + {_ps(inst_log)} + '"')) -Wait -PassThru
if ($p.ExitCode -ne 0) {{ Restore-Previous ('el instalador terminó con el código ' + $p.ExitCode + '.') }}
$v = Start-Process -FilePath $exe -ArgumentList '--verificar-actualizacion' -Wait -PassThru
if ($v.ExitCode -ne 0) {{ Restore-Previous 'la versión nueva no ha superado la comprobación.' }}
Start-Process -FilePath $exe
exit 0
"""


def _counts(db) -> dict:
    out = {}
    for t in TABLES_TO_CHECK:
        try:
            out[t] = db.scalar(f"SELECT COUNT(*) FROM {t}")
        except Exception:
            out[t] = None
    return out


def verify_after_update(layout: Layout, cfg) -> int:
    """--verificar-actualizacion: lo ejecuta el instalador con la versión NUEVA antes de abrirla."""
    marker = read_marker(layout) or {}
    try:
        if marker.get("to") and parse_version(marker["to"]) != parse_version(APP_VERSION):
            raise UpdateError(f"Se esperaba la versión {marker['to']} y se ha instalado la {APP_VERSION}.")
        detail = ""
        if cfg.is_host:
            from .server.db import Database
            from .server.main import DB_NAME
            db_path = cfg.server_data_dir / DB_NAME
            if db_path.exists():
                db = Database(db_path)  # aplica las migraciones (con copia previa) y comprueba el esquema
                try:
                    if not db.integrity_ok():
                        raise UpdateError("La base de datos no supera la comprobación de integridad.")
                    after = _counts(db)
                finally:
                    db.close()
                before = marker.get("counts") or {}
                lost = [t for t, n in before.items() if n is not None and (after.get(t) or 0) < n]
                if lost:
                    raise UpdateError("Faltan datos tras la actualización en: " + ", ".join(lost) + ".")
                detail = (f" Datos comprobados: {after.get('incidents')} incidencias, {after.get('tasks')} tareas, "
                          f"{after.get('messages')} mensajes, {after.get('users')} usuarios.")
        marker.update(result="ok", finished_at=datetime.now().isoformat())
        write_marker(layout, marker)
        write_log(layout, f"OK: actualizado de {marker.get('from', '?')} a {APP_VERSION} correctamente.{detail}")
        return 0
    except Exception as exc:
        marker.update(result="fallo", error=str(exc), finished_at=datetime.now().isoformat())
        write_marker(layout, marker)
        write_log(layout, f"La comprobación de la versión {APP_VERSION} ha fallado: {exc}")
        return 1


def revert_data(layout: Layout, cfg) -> int:
    """--revertir-datos: lo ejecuta la versión ANTERIOR tras una actualización fallida."""
    marker = read_marker(layout) or {}
    try:
        if cfg.is_host and marker.get("backup"):
            from .server.backups import restore_files
            from .server.main import DB_NAME
            zip_path = layout.backups_dir / marker["backup"]
            restore_files(zip_path, cfg.server_data_dir, cfg.server_data_dir / DB_NAME, "tras-actualizacion-fallida")
            write_log(layout, f"Datos restaurados desde la copia {marker['backup']} (hecha justo antes de actualizar).")
        marker.update(result="revertida", reverted_to=APP_VERSION, finished_at=datetime.now().isoformat())
        write_marker(layout, marker)
        write_log(layout, f"Se ha vuelto a la versión {APP_VERSION}. Tus datos están a salvo.")
        return 0
    except Exception as exc:
        write_log(layout, f"No se pudieron restaurar los datos automáticamente: {exc}. Usa una copia de "
                          "Documentos\\DEV CONTROL\\Copias de seguridad.")
        return 1


def acknowledge_last_update(layout: Layout) -> None:
    marker = read_marker(layout)
    if marker and marker.get("result"):
        marker["shown"] = True
        write_marker(layout, marker)
