"""Diagnóstico de conexión y de carpetas (no modifica ningún dato)."""
from __future__ import annotations

import http.client
import json
import socket

from . import APP_NAME, APP_VERSION
from .paths import is_synced_folder


def _line(ok: bool | None, text: str, fix: str = "") -> None:
    mark = {True: "[OK]   ", False: "[FALLO]", None: "[INFO] "}[ok]
    print(f"  {mark} {text}")
    if fix and ok is False:
        print(f"          Cómo solucionarlo: {fix}")


def run_diagnostics(layout, cfg) -> int:
    print(f"=== {APP_NAME} {APP_VERSION} · diagnóstico ===\n")
    _line(None, f"Este equipo: {socket.gethostname()} ({'anfitrión' if cfg.is_host else 'miembro'})")
    _line(None, f"Configuración: {cfg.path}")
    _line(None, f"Copias de seguridad: {layout.backups_dir}")
    data = cfg.server_data_dir
    if cfg.is_host:
        _line(None, f"Base de datos: {data}")
        if is_synced_folder(data):
            _line(False, "La carpeta de datos está dentro de OneDrive u otra carpeta sincronizada.",
                  "Deja vacío «carpeta_datos» en la configuración para usar la ubicación por defecto.")
    host, port = cfg.default_server_address()
    print(f"\nServidor por defecto: {host}:{port}")
    failures = 0
    try:
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request("GET", "/api/health")
        resp = conn.getresponse()
        info = json.loads(resp.read() or b"{}")
        conn.close()
        if info.get("app") == APP_NAME:
            _line(True, f"Responde DEV CONTROL {info.get('version')} (protocolo {info.get('protocol_version')}).")
            if info.get("recovery"):
                _line(False, "El servidor está en modo recuperación.", "Abre DEV CONTROL en el anfitrión y restaura una copia.")
                failures += 1
        else:
            _line(False, f"En {host}:{port} responde otro programa.", "Revisa la dirección y el puerto del servidor.")
            failures += 1
    except ConnectionRefusedError:
        _line(False, f"{host} responde pero DEV CONTROL no está en marcha en el puerto {port}.",
              "Abre DEV CONTROL en el anfitrión (o inicia el servidor desde el icono de la bandeja).")
        failures += 1
    except (OSError, ValueError, http.client.HTTPException) as exc:
        _line(False, f"No se puede conectar con {host}:{port} ({exc}).",
              "Comprueba que el anfitrión está encendido y que los dos equipos están en la misma red o VPN.")
        failures += 1
    print("\nResultado:", "todo correcto." if not failures else f"{failures} problema(s) encontrados.")
    return 0 if not failures else 1
