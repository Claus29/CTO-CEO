"""Compila DEV CONTROL para Windows: ejecutable (PyInstaller) + instalador (Inno Setup).

Uso (en Windows, con las dependencias de requirements-windows.txt instaladas):
    python tools/build_windows.py
Resultado: dist/DEVCONTROL-Setup-<versión>.exe
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from devcontrol import APP_VERSION, parse_version  # noqa: E402

VERSION_INFO = """VSVersionInfo(
  ffi=FixedFileInfo(filevers=({a}, {b}, {c}, 0), prodvers=({a}, {b}, {c}, 0), mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040A04B0', [
      StringStruct('CompanyName', 'DEV CONTROL'),
      StringStruct('FileDescription', 'DEV CONTROL'),
      StringStruct('FileVersion', '{v}'),
      StringStruct('InternalName', 'DEV CONTROL'),
      StringStruct('OriginalFilename', 'DEV CONTROL.exe'),
      StringStruct('ProductName', 'DEV CONTROL'),
      StringStruct('ProductVersion', '{v}')])]),
    VarFileInfo([VarStruct('Translation', [0x040A, 1200])])]
)
"""


def find_iscc() -> str:
    for candidate in (shutil.which("iscc"), r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
                      r"C:\Program Files\Inno Setup 6\ISCC.exe",
                      os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe")):
        if candidate and Path(candidate).exists():
            return candidate
    sys.exit("No se encuentra Inno Setup 6 (ISCC.exe). Instálalo desde https://jrsoftware.org/isdl.php")


def main() -> int:
    a, b, c = parse_version(APP_VERSION)
    (ROOT / "packaging" / "version_info.txt").write_text(VERSION_INFO.format(a=a, b=b, c=c, v=APP_VERSION),
                                                         encoding="utf-8")
    for d in (ROOT / "build", ROOT / "dist" / "DEV CONTROL"):
        shutil.rmtree(d, ignore_errors=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", str(ROOT / "dist"),
                    "--workpath", str(ROOT / "build"), str(ROOT / "packaging" / "devcontrol.spec")], check=True)
    exe = ROOT / "dist" / "DEV CONTROL" / "DEV CONTROL.exe"
    out = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=60)
    print("Ejecutable:", exe, out.stdout.strip() or "(sin salida: aplicación de ventana)")
    subprocess.run([find_iscc(), f"/DAppVersion={APP_VERSION}", str(ROOT / "packaging" / "installer.iss")], check=True)
    setup = ROOT / "dist" / f"DEVCONTROL-Setup-{APP_VERSION}.exe"
    print("Instalador:", setup, f"({setup.stat().st_size / 1_048_576:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
