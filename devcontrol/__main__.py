"""Punto de entrada de DEV CONTROL.

  DEV CONTROL.exe                     abre el programa (ventana + icono en la bandeja)
  DEV CONTROL.exe --inicio            arranca oculto en la bandeja (inicio con Windows)
  DEV CONTROL.exe --sin-ventana       arranca sin ventana ni bandeja (pruebas, servidores sin escritorio)
  DEV CONTROL.exe --copia             crea una copia de seguridad (con el servidor detenido)
  DEV CONTROL.exe --diagnostico       comprueba la conexión con el servidor y muestra las carpetas
  DEV CONTROL.exe --version           muestra la versión
  --verificar-actualizacion / --revertir-datos  los usa el actualizador
"""
from __future__ import annotations

import argparse
import sys

from . import APP_NAME, APP_VERSION


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog=APP_NAME, description=f"{APP_NAME} {APP_VERSION}")
    p.add_argument("--inicio", action="store_true", help="arrancar oculto en la bandeja")
    p.add_argument("--sin-ventana", action="store_true", help="sin ventana ni bandeja")
    p.add_argument("--puerto-local", type=int, help="puerto de la interfaz (solo para pruebas)")
    p.add_argument("--copia", action="store_true", help="crear una copia de seguridad")
    p.add_argument("--diagnostico", action="store_true", help="comprobar la conexión")
    p.add_argument("--version", action="store_true")
    p.add_argument("--verificar-actualizacion", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--revertir-datos", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--abrir", default="", help=argparse.SUPPRESS)   # devcontrol://aviso/<id> (avisos de Windows)
    args = p.parse_args(argv)

    if args.version:
        print(f"{APP_NAME} {APP_VERSION}")
        return 0
    from .config import ConfigError, load_config
    from .paths import default_layout
    layout = default_layout().ensure()
    try:
        cfg = load_config(layout)
    except ConfigError as exc:
        from .winutil import message_box
        message_box(f"Hay un error en la configuración:\n\n{exc}")
        return 2
    if args.verificar_actualizacion:
        from .updater import verify_after_update
        return verify_after_update(layout, cfg)
    if args.revertir_datos:
        from .updater import revert_data
        return revert_data(layout, cfg)
    if args.copia:
        return cmd_backup(layout, cfg)
    if args.diagnostico:
        from .diagnostics import run_diagnostics
        if not getattr(sys, "frozen", False):
            return run_diagnostics(layout, cfg)
        # El programa instalado no tiene consola: el informe se guarda en un archivo y se abre.
        import contextlib

        from .winutil import open_path
        report = layout.logs_dir / "diagnostico.txt"
        with open(report, "w", encoding="utf-8") as fh, contextlib.redirect_stdout(fh):
            code = run_diagnostics(layout, cfg)
        open_path(report)
        return code
    from .desktop import App
    from .toast import parse_link
    app = App(layout, headless=args.sin_ventana, port=args.puerto_local)
    return app.run(start_hidden=args.inicio, open_target=parse_link(args.abrir))


def cmd_backup(layout, cfg) -> int:
    if not cfg.is_host:
        print("Este equipo no es el anfitrión: las copias se hacen en el equipo que guarda los datos.")
        return 1
    from .server.backups import BackupManager
    from .server.db import Database
    from .server.main import DB_NAME
    path = cfg.server_data_dir / DB_NAME
    if not path.exists():
        print(f"No hay base de datos en {path}.")
        return 1
    db = Database(path)
    try:
        info = BackupManager(db, cfg.server_data_dir, layout.backups_dir).create("manual")
    finally:
        db.close()
    print(f"Copia creada: {layout.backups_dir / info['name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
