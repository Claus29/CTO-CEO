"""Ubicación de los datos, siempre fuera de la carpeta del programa.

  Documentos\\DEV CONTROL\\   copias, actualizaciones, exportaciones y configuración (visible para el usuario)
  %LOCALAPPDATA%\\DEV CONTROL\\  base de datos en uso, adjuntos, cola del cliente y registros

La base de datos en uso no se guarda en Documentos porque OneDrive suele
sincronizar esa carpeta y puede bloquear o dañar una base de datos abierta.
La variable de entorno DEVCONTROL_HOME permite usar otra carpeta raíz (pruebas
o instalación portátil).
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from . import APP_NAME

IS_WINDOWS = os.name == "nt"


def _known_documents_folder() -> Path | None:
    if not IS_WINDOWS:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                        ("Data4", ctypes.c_ubyte * 8)]

        # FOLDERID_Documents = {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
        guid = GUID(0xFDD39AD0, 0x238F, 0x46AF, (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
        out = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) == 0:
            path = Path(out.value)
            ctypes.windll.ole32.CoTaskMemFree(out)
            return path
    except Exception:
        pass
    return None


def documents_dir() -> Path:
    return _known_documents_folder() or (Path.home() / "Documents")


def local_app_dir() -> Path:
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base)
    return Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))


def is_synced_folder(path: Path) -> bool:
    """True si la carpeta está dentro de OneDrive u otra carpeta sincronizada conocida."""
    text = str(path).lower()
    if "onedrive" in text or "dropbox" in text or "google drive" in text:
        return True
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        root = os.environ.get(var)
        if root and text.startswith(root.lower()):
            return True
    return False


@dataclass
class Layout:
    docs_root: Path
    local_root: Path

    # -- carpetas visibles (Documentos) -----------------------------------
    @property
    def backups_dir(self) -> Path:
        return self.docs_root / "Copias de seguridad"

    @property
    def updates_dir(self) -> Path:
        return self.docs_root / "Actualizaciones"

    @property
    def exports_dir(self) -> Path:
        return self.docs_root / "Exportaciones"

    @property
    def config_dir(self) -> Path:
        return self.docs_root / "Configuración"

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.ini"

    @property
    def readme_file(self) -> Path:
        return self.docs_root / "LÉEME.txt"

    # -- carpetas internas (AppData) ----------------------------------------
    @property
    def server_dir(self) -> Path:
        return self.local_root / "Servidor"

    @property
    def client_root(self) -> Path:
        return self.local_root / "Cliente"

    @property
    def logs_dir(self) -> Path:
        return self.local_root / "Registros"

    @property
    def install_file(self) -> Path:
        """Valores elegidos en el instalador (rol, servidor, puerto…)."""
        return self.local_root / "instalacion.ini"

    @property
    def window_dir(self) -> Path:
        return self.local_root / "Ventana"

    def ensure(self) -> Layout:
        for p in (self.backups_dir, self.updates_dir, self.exports_dir, self.config_dir, self.local_root,
                  self.client_root, self.logs_dir):
            p.mkdir(parents=True, exist_ok=True)
        if not self.readme_file.exists():
            self.readme_file.write_text(README_TEXT.format(local=self.local_root), encoding="utf-8")
        return self


README_TEXT = """DEV CONTROL · Carpeta de datos
==============================

Esta carpeta guarda tu información de DEV CONTROL. NO la borres.

  Copias de seguridad\\   Copias completas (base de datos + adjuntos). Se crean solas cada día,
                          al detener el servidor y antes de cada actualización.
  Actualizaciones\\       Instaladores descargados y registro de actualizaciones (registro.txt).
  Exportaciones\\         Informes PDF y CSV generados desde el programa.
  Configuración\\         config.ini: rol de este equipo, servidor, puertos… (editable con el Bloc de notas).

La base de datos en uso y los adjuntos están en:
  {local}\\Servidor

Desinstalar o reinstalar DEV CONTROL nunca borra estas carpetas.
"""


def default_layout() -> Layout:
    home = os.environ.get("DEVCONTROL_HOME")
    if home:
        root = Path(home)
        return Layout(docs_root=root / "Documentos", local_root=root / "Local")
    return Layout(docs_root=documents_dir() / APP_NAME, local_root=local_app_dir() / APP_NAME)


def program_dir() -> Path:
    """Carpeta del programa (la del .exe si está empaquetado)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


WEB_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "devcontrol" / "web" \
    if getattr(sys, "frozen", False) else Path(__file__).resolve().parent / "web"
