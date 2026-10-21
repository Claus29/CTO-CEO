"""Pequeñas utilidades del sistema (Windows, con alternativas para desarrollo en Linux/macOS)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import APP_NAME

IS_WINDOWS = os.name == "nt"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def message_box(text: str, title: str = APP_NAME, error: bool = True) -> None:
    """Mensaje comprensible: ventana en Windows, texto en la consola en otros sistemas."""
    if IS_WINDOWS:
        try:
            import ctypes
            flags = 0x10 if error else 0x40  # MB_ICONERROR / MB_ICONINFORMATION
            ctypes.windll.user32.MessageBoxW(None, text, title, flags | 0x00010000)
            return
        except Exception:
            pass
    print(f"[{title}] {text}", file=sys.stderr if error else sys.stdout)


def ask_yes_no(text: str, title: str = APP_NAME) -> bool:
    if IS_WINDOWS:
        try:
            import ctypes
            return ctypes.windll.user32.MessageBoxW(None, text, title, 0x04 | 0x30 | 0x00010000) == 6  # Sí
        except Exception:
            pass
    return input(f"{text} [s/N] ").strip().lower() in ("s", "si", "sí", "y", "yes")


def open_path(path: Path) -> None:
    """Abre una carpeta o archivo con el programa predeterminado del sistema."""
    path = Path(path)
    if path.is_dir():
        path.mkdir(parents=True, exist_ok=True)
    if IS_WINDOWS:
        os.startfile(str(path))  # noqa: S606 - ruta local controlada por el programa
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def executable_command() -> list[str]:
    """Orden para volver a abrir este mismo programa."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "devcontrol"]


def set_autostart(enabled: bool) -> None:
    """Inicio con Windows (clave Run del usuario, sin permisos de administrador)."""
    if not IS_WINDOWS:
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            cmd = " ".join(f'"{p}"' for p in executable_command()) + " --inicio"
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass


def allow_in_firewall(port: int, call_port: int | None = None, relay_ports: tuple | None = None) -> bool:
    """Pide permiso de administrador (una vez) para abrir el servidor y las llamadas solo a redes privadas."""
    if not IS_WINDOWS:
        return False
    import ctypes
    remote = "localsubnet,100.64.0.0/10,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
    rules = [f'name="{APP_NAME} (servidor)" dir=in action=allow protocol=TCP localport={port}']
    if call_port:
        rules.append(f'name="{APP_NAME} (llamadas TCP)" dir=in action=allow protocol=TCP localport={call_port}')
        ports = f"{call_port}" + (f",{relay_ports[0]}-{relay_ports[1]}" if relay_ports else "")
        rules.append(f'name="{APP_NAME} (llamadas UDP)" dir=in action=allow protocol=UDP localport={ports}')
    if getattr(sys, "frozen", False):
        rules.append(f'name="{APP_NAME} (programa)" dir=in action=allow program="{sys.executable}"')
    # Se borran las reglas anteriores con el mismo nombre para no duplicarlas.
    cmds = [f'netsh advfirewall firewall delete rule name="{r.split(chr(34))[1]}" >nul 2>&1' for r in rules]
    cmds += [f"netsh advfirewall firewall add rule {r} profile=private,domain remoteip={remote}" for r in rules]
    script = " & ".join(cmds)
    return ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", f"/c {script}", None, 0) > 32


def computer_name() -> str:
    import socket
    return os.environ.get("COMPUTERNAME") or socket.gethostname() or "equipo"


# -- datos protegidos para este usuario de Windows (DPAPI) -------------------------------------
def protect(data: bytes) -> bytes:
    """Cifra con la cuenta de Windows del usuario (solo él, en este equipo, puede descifrarlo)."""
    if not IS_WINDOWS:
        return b"plain:" + data
    blob_out = _dpapi(data, protect=True)
    return b"dpapi:" + blob_out


def unprotect(data: bytes) -> bytes:
    if data.startswith(b"plain:"):
        return data[6:]
    if data.startswith(b"dpapi:") and IS_WINDOWS:
        return _dpapi(data[6:], protect=False)
    raise ValueError("Formato desconocido")


def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out)):   # UI_FORBIDDEN
        raise OSError("No se pudo proteger el dato con la cuenta de Windows.")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)
