"""Programa de escritorio: una sola instancia, servidor del anfitrión, ventana nativa e icono en la bandeja.

  * La ventana usa WebView2 (el motor de Edge que ya trae Windows) a través de pywebview.
  * El icono de la bandeja muestra el estado con colores y permite iniciar, detener y cerrar el servidor.
  * Cerrar la ventana la oculta (el servidor y los avisos siguen funcionando); «Salir» en la bandeja
    cierra todo y, si este equipo es el anfitrión, hace una copia de seguridad al detener el servidor.
"""
from __future__ import annotations

import configparser
import http.client
import json
import os
import sys
import threading
import time
from pathlib import Path

from . import APP_NAME, APP_VERSION
from .config import ConfigError, load_config, server_base_url
from .logs import setup_logging
from .paths import Layout, default_layout, is_synced_folder
from .winutil import message_box, open_path, set_autostart

TRAY_COLORS = {"ok": (63, 191, 134), "warn": (240, 168, 58), "bad": (239, 83, 80), "idle": (138, 147, 163)}


class App:
    def __init__(self, layout: Layout | None = None, headless: bool = False, port: int | None = None):
        self.layout = (layout or default_layout()).ensure()
        self.cfg = load_config(self.layout)
        self.headless = headless
        self.log = setup_logging(self.layout.logs_dir, "programa")
        self.server = None
        self.server_error = ""
        self.window = None
        self.tray = None
        self.stop_event = threading.Event()
        self._server_lock = threading.RLock()
        from .agent.main import Agent
        from .updater import Updater
        self.updater = Updater(self.layout, self.cfg, hooks={"prepare_update": self.prepare_update, "quit": self.quit,
                                                             "server_url": self._server_url,
                                                             "auto_scheduled": self._auto_scheduled,
                                                             "busy": self._busy_reason})
        self.agent = Agent(self.cfg, self.layout, updater=self.updater)
        if port is not None:
            self.agent.local_port = port
        self.agent.hooks.update(open_path=open_path, show=self.show_window, quit=self.quit, toast=self.toast,
                                open_route=self.open_route)
        if self.cfg.is_host:
            self.agent.hooks.update(host_status=self.host_status, host_start=self.start_server, host_stop=self.stop_server)

    def _server_url(self) -> str | None:
        """Servidor del que los miembros reciben las actualizaciones (el anfitrión)."""
        if self.cfg.is_host:
            return None
        return server_base_url(self.agent.server_scheme, self.agent.server_host, self.agent.server_port)

    # -- actualización automática -----------------------------------------------------------
    def _auto_scheduled(self, version: str, seconds: int) -> None:
        self.notify(f"{APP_NAME} {version} se instalará sola",
                    f"En {max(1, seconds // 60)} minutos DEV CONTROL se cerrará un momento, se actualizará y volverá a "
                    "abrirse. Guarda lo que estés escribiendo. Puedes posponerlo desde la ventana.", urgent=True)

    def _busy_reason(self) -> str | None:
        """Motivos para esperar antes de instalar: una llamada en este equipo o, en el anfitrión, en el servidor."""
        if self.agent.notifier.in_call:
            return "Esperando a que termine tu llamada para actualizar."
        server = self.server
        hub = getattr(server, "calls", None)
        if hub is not None and getattr(hub, "calls", None):
            return "Hay una llamada en curso en el equipo: se actualizará al terminar."
        return None

    def check_updates_now(self) -> None:
        """«Buscar actualizaciones» de la bandeja: busca y dice siempre el resultado."""
        self.updater.check(force=True)
        st = self.updater.status()
        if st.get("error"):
            self.notify("No se pudo buscar actualizaciones", st["error"])
        elif st.get("auto_in") is not None:
            self.notify(f"Hay una versión nueva: {st['latest']}",
                        f"Se instalará sola en {max(1, st['auto_in'] // 60)} minutos.")
        elif st.get("available"):
            self.notify(f"Hay una versión nueva: {st['latest']}", st.get("auto_blocked") or st.get("reason") or
                        "Abre DEV CONTROL → Ajustes → Servidor y actualizaciones.")
        else:
            self.notify("DEV CONTROL está al día", f"Tienes la última versión ({st['current']}).")

    # -- servidor (solo anfitrión) ---------------------------------------------------
    def _import_path(self) -> Path | None:
        ini = configparser.ConfigParser()
        try:
            ini.read(self.layout.install_file, encoding="utf-8-sig")
        except configparser.Error:
            return None
        raw = ini.get("instalacion", "importar_desde", fallback="").strip()
        if not raw:
            return None
        p = Path(raw)
        if p.is_dir():
            for folder in (p / "datos", p):
                found = sorted(folder.glob("*.db")) if folder.is_dir() else []
                if found:
                    return found[0]
        return p if p.is_file() else None

    def start_server(self) -> None:
        if not self.cfg.is_host:
            return
        with self._server_lock:
            if self.server is not None:
                return
            from .server.main import ServerApp
            try:
                if is_synced_folder(self.cfg.server_data_dir):
                    self.log.warning("La carpeta de datos %s está sincronizada (OneDrive…): puede dañar la base de datos",
                                     self.cfg.server_data_dir)
                server = ServerApp(self.cfg, self.layout, import_from=self._import_path())
                server.start()
                self.server, self.server_error = server, ""
                self.log.info("Servidor iniciado (%s)", ", ".join(server.listening))
            except OSError as exc:
                self.server_error = (f"No se puede usar el puerto {self.cfg.server_port}: {exc}. ¿Hay otro programa "
                                     "usándolo o DEV CONTROL ya está abierto con otro usuario de Windows?")
                self.log.error(self.server_error)
            except Exception as exc:
                self.server_error = f"No se pudo iniciar el servidor: {exc}"
                self.log.exception("No se pudo iniciar el servidor")
        self.agent.check_health()
        self.refresh_tray()

    def stop_server(self, backup: bool = True) -> None:
        with self._server_lock:
            server, self.server = self.server, None
        if server is not None:
            server.shutdown(backup=backup)
            self.log.info("Servidor detenido")
        self.agent.check_health()
        self.refresh_tray()

    def host_status(self) -> dict:
        s = self.server
        return {"running": s is not None and not s.stop_event.is_set(), "error": self.server_error,
                "listening": s.listening if s else [], "listen_errors": s.listen_errors if s else [],
                "recovery": bool(s and s.recovery), "data_dir": str(self.cfg.server_data_dir),
                "backups_dir": str(self.layout.backups_dir), "port": self.cfg.server_port}

    def prepare_update(self) -> dict:
        """Antes de instalar una versión nueva: detiene el servidor y hace una copia con los datos cerrados."""
        if not self.cfg.is_host:
            return {}
        self.stop_server(backup=False)
        from .server.backups import BackupManager
        from .server.db import Database
        from .server.main import DB_NAME
        from .updater import _counts
        db_path = self.cfg.server_data_dir / DB_NAME
        if not db_path.exists():
            return {}
        db = Database(db_path)
        try:
            info = BackupManager(db, self.cfg.server_data_dir, self.layout.backups_dir).create("previa-actualizacion")
            return {"backup": info["name"], "counts": _counts(db)}
        finally:
            db.close()

    # -- una sola instancia ------------------------------------------------------------
    def other_instance(self, open_target: str = "") -> bool:
        """Si DEV CONTROL ya está abierto en este equipo (en el mismo puerto), le pide que se muestre
        (y, si viene de pulsar un aviso de Windows, que abra ese aviso)."""
        port = self.agent.local_port
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            conn.request("GET", "/local/status", headers={"Host": f"127.0.0.1:{port}"})
            resp = conn.getresponse()
            data = json.loads(resp.read() or b"{}")
            conn.close()
        except (OSError, ValueError, http.client.HTTPException):
            return False
        if data.get("app") != APP_NAME:
            return False
        try:
            secret = (self.layout.client_root / ".secreto_local").read_text(encoding="utf-8").strip()
            body = json.dumps({"open": open_target}).encode() if open_target else b""
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            conn.request("POST", "/local/show", body=body,
                         headers={"Host": f"127.0.0.1:{port}", "X-DevControl-Local-Secret": secret,
                                  "Content-Length": str(len(body))})
            conn.getresponse().read()
            conn.close()
        except OSError:
            pass
        return True

    # -- ejecución ----------------------------------------------------------------------
    def run(self, start_hidden: bool = False, open_target: str = "") -> int:
        if self.other_instance(open_target):
            self.log.info("DEV CONTROL ya estaba abierto en el puerto %s: se muestra esa ventana", self.agent.local_port)
            if self.headless:
                print(f"{APP_NAME} ya está abierto en http://127.0.0.1:{self.agent.local_port}/", flush=True)
            return 0
        if self.cfg.is_host:
            self.start_server()
        try:
            self.agent.start()
        except OSError as exc:
            message_box(f"No se puede abrir DEV CONTROL: el puerto local {self.cfg.local_port} está ocupado ({exc}).\n\n"
                        f"Cambia «puerto_local» en {self.cfg.path} y vuelve a abrirlo.")
            self.stop_server()
            return 1
        threading.Thread(target=self.updater.loop, name="actualizaciones", daemon=True).start()
        if self.cfg.is_host and self.cfg.keep_awake and sys.platform == "win32":
            threading.Thread(target=self._keep_awake, name="mantener-despierto", daemon=True).start()
        if open_target:   # arrancado al pulsar un aviso: la interfaz lo abrirá al cargar
            self.agent.request_open(open_target, show=False)
        try:
            from .paths import WEB_DIR
            from .toast import register_app
            register_app(icon=str(WEB_DIR / "img" / "icono.png"))
        except Exception:
            self.log.warning("No se pudieron registrar los avisos de Windows", exc_info=True)
        try:
            set_autostart(self.cfg.autostart)
        except OSError:
            self.log.warning("No se pudo configurar el inicio con Windows")
        self.log.info("%s %s abierto (%s)", APP_NAME, APP_VERSION, "anfitrión" if self.cfg.is_host else "miembro")
        if self.headless:
            import signal
            try:
                signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=self.quit, daemon=True).start())
            except ValueError:
                pass
            print(f"{APP_NAME} {APP_VERSION} en {self.agent.url} (Ctrl+C para salir)", flush=True)
            try:
                while not self.stop_event.wait(1):
                    pass
            except KeyboardInterrupt:
                self.quit()
            return 0
        self.start_tray()
        return self.run_window(start_hidden)

    def _keep_awake(self) -> None:
        """El anfitrión no entra en suspensión mientras DEV CONTROL esté abierto (la pantalla sí puede apagarse).

        Así los demás equipos, también los que entran por Internet, lo encuentran siempre disponible."""
        import ctypes
        es_continuous, es_system_required = 0x80000000, 0x00000001
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(es_continuous | es_system_required)
            self.log.info("El equipo no se suspenderá mientras DEV CONTROL esté abierto ([escritorio] mantener_despierto)")
            self.stop_event.wait()
        except Exception as exc:
            self.log.warning("No se pudo evitar la suspensión del equipo: %s", exc)
        finally:
            try:
                ctypes.windll.kernel32.SetThreadExecutionState(es_continuous)
            except Exception:
                pass

    def run_window(self, start_hidden: bool) -> int:
        try:
            import webview  # pywebview (WebView2)
        except ImportError:
            import webbrowser
            self.log.warning("pywebview no está disponible: se abre el navegador")
            webbrowser.open(self.agent.url)
            try:
                while not self.stop_event.wait(1):
                    pass
            except KeyboardInterrupt:
                self.quit()
            return 0
        self.window = webview.create_window(APP_NAME, self.agent.url, width=1380, height=880, min_size=(980, 640),
                                            hidden=start_hidden, text_select=True, background_color="#0f1115")

        def on_closing():
            if self.cfg.minimize_on_close and not self.stop_event.is_set():
                self.window.hide()
                if self.tray is not None and not getattr(self, "_told_tray", False):
                    self._told_tray = True
                    self.notify(APP_NAME, "DEV CONTROL sigue funcionando aquí. Usa «Salir» para cerrarlo del todo.")
                return False
            return True

        self.window.events.closing += on_closing
        self.window.events.loaded += self._grant_media_permissions
        self.agent.hooks["notify"] = self.notify
        storage = self.layout.window_dir
        storage.mkdir(parents=True, exist_ok=True)
        webview.start(private_mode=False, storage_path=str(storage), gui="edgechromium" if os.name == "nt" else None)
        self.quit()
        return 0

    def _grant_media_permissions(self) -> None:
        """Concede micrófono, cámara y pantalla a la propia interfaz (solo 127.0.0.1) sin preguntar cada vez."""
        if getattr(self, "_granted", False) or os.name != "nt":
            return
        self._granted = True
        try:
            from Microsoft.Web.WebView2.Core import CoreWebView2PermissionKind as Kind  # type: ignore
            from Microsoft.Web.WebView2.Core import CoreWebView2PermissionState as State  # type: ignore
            from System import Action  # type: ignore
            form = self.window.native
            own = self.agent.url.rstrip("/")
            allowed = {Kind.Microphone, Kind.Camera, Kind.Notifications}
            for extra in ("WindowManagement", "OtherSensors"):
                if hasattr(Kind, extra):
                    allowed.add(getattr(Kind, extra))

            def on_permission(sender, args):
                if str(args.Uri).startswith(own) and args.PermissionKind in allowed:
                    args.State = State.Allow

            def attach():
                form.webview.CoreWebView2.PermissionRequested += on_permission
            form.Invoke(Action(attach))
        except Exception:
            self.log.warning("No se pudieron conceder los permisos de micrófono y cámara automáticamente; "
                             "Windows los pedirá la primera vez.")

    def show_window(self) -> None:
        if self.window is not None:
            try:
                self.window.show()
                self.window.restore()
                # Traer delante aunque otra ventana tenga el foco (llamadas entrantes).
                self.window.on_top = True
                self.window.on_top = False
            except Exception:
                pass
        elif not self.headless:
            import webbrowser
            webbrowser.open(self.agent.url)

    def toast(self, title: str, body: str, link: str, urgent: bool = False, tag: str = "") -> None:
        """Aviso nativo de Windows (se puede pulsar para abrirlo); si no se puede, globo de la bandeja."""
        def run():
            from .toast import show_toast
            if not (getattr(self, "_toast_ok", True) and show_toast(title, body, link, urgent, tag)):
                if getattr(self, "_toast_ok", True) and os.name == "nt":
                    self._toast_ok = False   # no volver a intentarlo en esta sesión: se usa la bandeja
                    self.log.warning("Los avisos nativos de Windows no están disponibles; se usa la bandeja")
                self.notify(title, body, urgent)
        threading.Thread(target=run, name="aviso", daemon=True).start()

    def open_route(self, route: str) -> None:
        """Pulsar un aviso: la ventana se muestra y la interfaz abre ese aviso."""
        self.show_window()
        if self.window is not None:
            try:
                self.window.evaluate_js("window.dcCheckOpen && window.dcCheckOpen()")
            except Exception:
                pass

    def notify(self, title: str, body: str, urgent: bool = False) -> None:
        if self.tray is not None:
            try:
                self.tray.notify(body or title, title)
            except Exception:
                pass

    def quit(self) -> None:
        if self.stop_event.is_set():
            return
        self.stop_event.set()
        self.log.info("Cerrando DEV CONTROL")
        self.updater.stop_event.set()
        self.stop_server()
        self.agent.shutdown()
        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception:
                pass
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception:
                pass

    # -- bandeja del sistema ---------------------------------------------------------------
    def tray_state(self) -> tuple[str, str]:
        a = self.agent
        upd = self.updater.status()
        if self.cfg.is_host and self.server is None:
            return "bad", "Servidor detenido" + (f": {self.server_error}" if self.server_error else "")
        if not a.reachable:
            return "bad", "Sin conexión con el servidor"
        if upd.get("auto_in") is not None:
            return "warn", f"Se actualizará a la {upd.get('auto_version')} en {max(1, upd['auto_in'] // 60)} min"
        if upd.get("mandatory"):
            return "warn", "Actualización obligatoria pendiente"
        if upd.get("available"):
            return "warn", f"Versión obsoleta: hay una nueva ({upd.get('latest')})"
        if a.outbox.pending_count():
            return "warn", f"Enviando {a.outbox.pending_count()} cambios pendientes"
        return "ok", "Servidor en marcha" if self.cfg.is_host else "Conectado"

    def _icon_image(self, state: str):
        from PIL import Image, ImageDraw

        from .paths import WEB_DIR
        try:
            img = Image.open(WEB_DIR / "img" / "icono.png").convert("RGBA").resize((64, 64))
        except OSError:
            img = Image.new("RGBA", (64, 64), (27, 33, 48, 255))
        d = ImageDraw.Draw(img)
        r, g, b = TRAY_COLORS.get(state, TRAY_COLORS["idle"])
        d.ellipse((40, 40, 63, 63), fill=(r, g, b, 255), outline=(15, 17, 21, 255), width=3)
        return img

    def start_tray(self) -> None:
        try:
            import pystray
        except ImportError:
            self.log.warning("pystray no está disponible: sin icono en la bandeja")
            return
        item = pystray.MenuItem
        host = self.cfg.is_host
        menu = pystray.Menu(
            item("Abrir DEV CONTROL", lambda: self.show_window(), default=True),
            item(lambda _: self.tray_state()[1], None, enabled=False),
            pystray.Menu.SEPARATOR,
            item("Iniciar servidor", lambda: threading.Thread(target=self.start_server, daemon=True).start(),
                 visible=lambda _: host and self.server is None),
            item("Detener servidor", lambda: threading.Thread(target=self.stop_server, daemon=True).start(),
                 visible=lambda _: host and self.server is not None),
            item("Copia de seguridad ahora", lambda: threading.Thread(target=self.backup_now, daemon=True).start(),
                 visible=lambda _: host and self.server is not None),
            item("Permitir en el Firewall de Windows", lambda: self.allow_firewall(), visible=lambda _: host),
            item("Buscar actualizaciones", lambda: threading.Thread(target=self.check_updates_now, daemon=True).start()),
            item("Abrir la carpeta de datos", lambda: open_path(self.layout.docs_root)),
            pystray.Menu.SEPARATOR,
            item("Salir", lambda: threading.Thread(target=self.quit, daemon=True).start()),
        )
        state = self.tray_state()
        self.tray = pystray.Icon("DevControl", self._icon_image(state[0]), f"{APP_NAME} · {state[1]}", menu)
        self.tray.run_detached()
        self._tray_state = state[0]
        threading.Thread(target=self._tray_loop, name="bandeja", daemon=True).start()

    def _tray_loop(self) -> None:
        last_obsolete = 0.0
        while not self.stop_event.wait(3):
            self.refresh_tray()
            # Mientras haya una versión más nueva, se avisa al abrir y cada 4 horas.
            upd = self.updater.status()
            if (upd.get("available") or upd.get("mandatory")) and time.monotonic() - last_obsolete >= 4 * 3600 \
                    or (upd.get("available") and not last_obsolete):
                last_obsolete = time.monotonic()
                if upd.get("auto_in") is None:   # si se va a instalar sola, ya se avisó al programarla
                    self.notify(f"{APP_NAME} está obsoleto",
                                f"Tienes la versión {upd.get('current')} y hay una nueva "
                                f"({upd.get('latest') or upd.get('required')}). Abre DEV CONTROL y pulsa «Actualizar ahora».")

    def refresh_tray(self) -> None:
        if self.tray is None:
            return
        try:
            state, text = self.tray_state()
            if state != getattr(self, "_tray_state", None):
                self.tray.icon = self._icon_image(state)
                self._tray_state = state
            self.tray.title = f"{APP_NAME} · {text}"[:120]
            self.tray.update_menu()
        except Exception:
            pass

    def backup_now(self) -> None:
        server = self.server
        if server is None:
            return
        try:
            info = server.backups.create("manual")
            self.notify(APP_NAME, f"Copia de seguridad creada: {info['name']}")
        except Exception as exc:
            self.notify(APP_NAME, f"No se pudo crear la copia: {exc}")

    def allow_firewall(self) -> None:
        from .winutil import allow_in_firewall
        if not allow_in_firewall(self.cfg.server_port, self.cfg.call_port, self.cfg.relay_ports):
            self.notify(APP_NAME, "No se pudo pedir permiso para el Firewall.")


def run_desktop(start_hidden: bool = False, headless: bool = False) -> int:
    try:
        app = App(headless=headless)
    except ConfigError as exc:
        message_box(f"Hay un error en la configuración:\n\n{exc}")
        return 2
    return app.run(start_hidden=start_hidden)


def wait_for(predicate, timeout: float = 10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.1)
    return False
