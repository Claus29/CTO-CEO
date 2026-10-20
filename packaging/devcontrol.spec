# Especificación de PyInstaller para DEV CONTROL (carpeta, sin consola).
# Uso: pyinstaller packaging/devcontrol.spec --noconfirm   (lo hace tools/build_windows.py)
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

a = Analysis(
    [os.path.join(SPECPATH, "launch.py")],
    pathex=[ROOT],
    datas=[(os.path.join(ROOT, "devcontrol", "web"), os.path.join("devcontrol", "web"))],
    hiddenimports=["pystray._win32", "webview.platforms.edgechromium", "webview.platforms.winforms", "clr"],
    excludes=["tkinter", "unittest", "pydoc", "test"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="DEV CONTROL",
    console=False,
    icon=os.path.join(SPECPATH, "devcontrol.ico"),
    version=os.path.join(SPECPATH, "version_info.txt") if os.path.exists(os.path.join(SPECPATH, "version_info.txt")) else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="DEV CONTROL")
