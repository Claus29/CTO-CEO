"""Avisos nativos de Windows 10/11 (Centro de actividades) sin dependencias nuevas.

Se muestran con PowerShell y las clases de Windows (WinRT) que ya trae el sistema. Al pulsar el aviso, Windows abre
`devcontrol://aviso/<id>`, que lanza DEV CONTROL; si ya estaba abierto, le pasa el aviso y se cierra.
Si algo falla (Windows antiguo, directivas que bloquean PowerShell…), quien llama usa el globo de la bandeja.
"""
from __future__ import annotations

import base64
import os
import subprocess
import sys
from xml.sax.saxutils import escape, quoteattr

from . import APP_NAME

AUMID = "DevControl.App"          # identificador de la aplicación para Windows (agrupa los avisos)
PROTOCOL = "devcontrol"


def toast_xml(title: str, body: str, launch: str, urgent: bool = False) -> str:
    # Lo urgente se queda en pantalla hasta que se atiende (escenario «recordatorio», que exige un botón).
    scenario = ' scenario="reminder"' if urgent else ""
    actions = (f'<actions><action content="Abrir" activationType="protocol" arguments={quoteattr(launch)}/>'
               "</actions>") if urgent else ""
    sound = "Reminder" if urgent else "Default"
    return (f"<toast activationType=\"protocol\" launch={quoteattr(launch)}{scenario}>"
            "<visual><binding template=\"ToastGeneric\">"
            f"<text>{escape(title[:120])}</text><text>{escape(body[:300])}</text>"
            f"</binding></visual>{actions}<audio src=\"ms-winsoundevent:Notification.{sound}\"/></toast>")


def powershell_script(xml: str, tag: str = "", dry: bool = False) -> str:
    """dry=True prepara el aviso sin mostrarlo (lo usa la comprobación automática en Windows)."""
    data = base64.b64encode(xml.encode("utf-8")).decode()
    safe_tag = "".join(ch for ch in tag if ch.isalnum() or ch in "-_#")[:60]
    return (
        "$ErrorActionPreference='Stop';"
        "[void][Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime];"
        "[void][Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom.XmlDocument,ContentType=WindowsRuntime];"
        "$x=New-Object Windows.Data.Xml.Dom.XmlDocument;"
        f"$x.LoadXml([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{data}')));"
        "$t=New-Object Windows.UI.Notifications.ToastNotification $x;"
        + (f"$t.Tag='{safe_tag}';$t.Group='devcontrol';" if safe_tag else "")
        + ("$null=[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('" + AUMID + "')"
           if dry else f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{AUMID}').Show($t)")
    )


def show_toast(title: str, body: str, launch: str, urgent: bool = False, tag: str = "", dry: bool = False) -> bool:
    if os.name != "nt":
        return False
    script = powershell_script(toast_xml(title, body, launch, urgent), tag, dry)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                            "-EncodedCommand", encoded], capture_output=True, timeout=20,
                           creationflags=0x08000000)   # CREATE_NO_WINDOW
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def register_app(exe: str | None = None, icon: str | None = None) -> bool:
    """Registra (para este usuario) el nombre e icono de los avisos y el enlace devcontrol:// al programa."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return False
    import winreg
    exe = exe or sys.executable
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(AUMID)
    except Exception:
        pass
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\AppUserModelId\{AUMID}") as k:
            winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
            if icon:
                winreg.SetValueEx(k, "IconUri", 0, winreg.REG_SZ, icon)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROTOCOL}") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, f"URL:{APP_NAME}")
            winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROTOCOL}\DefaultIcon") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, f'"{exe}",0')
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROTOCOL}\shell\open\command") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, f'"{exe}" --abrir "%1"')
        return True
    except OSError:
        return False


def parse_link(link: str) -> str:
    """«devcontrol://aviso/123» → «aviso/123». Cualquier otra cosa se ignora."""
    link = (link or "").strip()
    prefix = f"{PROTOCOL}://"
    if not link.lower().startswith(prefix):
        return ""
    target = link[len(prefix):].strip("/")
    kind, _, ident = target.partition("/")
    return f"aviso/{ident}" if kind == "aviso" and ident.isdigit() and len(ident) < 12 else ""
