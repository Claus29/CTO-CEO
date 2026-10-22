"""Programa local: proxy, cola sin conexión con reenvío sin duplicados, caché y elección de servidor."""
import socket
import time
import uuid

import pytest
from conftest import ADMIN_PASSWORD, Client

from devcontrol.agent.main import Agent
from devcontrol.config import load_config
from devcontrol.server.main import ServerApp


def wait(pred, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.1)
    return False


@pytest.fixture
def stack(layout):
    cfg = load_config(layout)
    server = ServerApp(cfg, layout)
    port = server.start(port=0, listen=["127.0.0.1"])
    cfg.server_port = port
    agent = Agent(cfg, layout)
    agent.connect("")
    lport = agent.start(port=0)
    local = Client(f"http://127.0.0.1:{lport}")
    local.post("/api/setup", {"admin": {"username": "ana", "display_name": "Ana", "password": ADMIN_PASSWORD}})
    tok = local.post("/api/login", {"username": "ana", "password": ADMIN_PASSWORD})["token"]
    local.token = tok
    yield {"server": server, "agent": agent, "local": local, "cfg": cfg, "layout": layout, "port": port}
    agent.shutdown()
    server.shutdown(backup=False)


def test_serves_interface_and_status(stack):
    local = stack["local"]
    code, html = local.call("GET", "/")
    assert code == 200 and b"DEV CONTROL" in html
    st = local.get("/local/status")
    assert st["reachable"] and st["is_host_server"] and st["server_info"]["app"] == "DEV CONTROL"
    assert local.call("GET", "/../config.py")[0] == 404


def test_rejects_foreign_host_header(stack):
    code, _ = stack["local"].call("GET", "/local/status", headers={"Host": "evil.example:80"})
    assert code == 403


def test_offline_queue_is_sent_once_on_reconnect(stack, layout):
    local, agent, server = stack["local"], stack["agent"], stack["server"]
    server.shutdown(backup=False)
    body = {"uid": uuid.uuid4().hex, "title": "Creada sin conexión", "priority": "media", "category": "diseno"}
    code, data = local.call("POST", "/api/incidents", body, headers={"X-Offline-Queue": "1"})
    assert code == 202 and data["queued"]
    assert local.get("/local/status")["pending_count"] == 1
    # lecturas desde la caché mientras no hay conexión
    code, _ = local.call("GET", "/api/me")
    assert code in (200, 503)
    # vuelve el servidor (mismo puerto y datos)
    cfg = stack["cfg"]
    again = ServerApp(cfg, layout)
    again.start(port=stack["port"], listen=["127.0.0.1"])
    try:
        agent.flush_event.set()
        assert wait(lambda: agent.outbox.pending_count() == 0)
        items = local.get("/api/incidents")["items"]
        assert [i["title"] for i in items] == ["Creada sin conexión"]
        # reenviar la misma operación no la duplica
        local.call("POST", "/api/incidents", body)
        assert len(local.get("/api/incidents")["items"]) == 1
    finally:
        again.shutdown(backup=False)


def test_choose_other_server_and_test_connection(stack):
    local, agent = stack["local"], stack["agent"]
    bad = local.post("/local/test", {"server": "127.0.0.1:1"})
    assert not bad["ok"] and bad["message"]
    good = local.post("/local/test", {"server": f"127.0.0.1:{stack['port']}"})
    assert good["ok"] and good["version"]
    st = local.post("/local/connect", {"server": "127.0.0.1:1"})
    assert st["server"] == "127.0.0.1:1" and not st["reachable"]
    assert agent.outbox.path.parent.name == "127.0.0.1_1"        # cola y caché propias de ese servidor
    code, err = local.call("POST", "/local/connect", {"server": "no valido!"})
    assert code == 400
    st = local.post("/local/connect", {"server": ""})
    assert st["server_is_default"]
    local.post("/local/profiles", {"server": f"127.0.0.1:{stack['port']}", "label": "Ana"})


def test_save_export_to_documents(stack, layout):
    res = stack["local"].post("/local/save", {"path": "/api/export/incidents.csv", "folder": "exportaciones",
                                              "open": False})
    assert res["file"].startswith(str(layout.exports_dir)) and res["name"].endswith(".csv")


def test_old_server_version_marks_update(stack):
    stack["agent"].updater.note_server({"version": "9.0.0"})
    assert stack["local"].get("/local/update")["state"] == "idle"


def test_local_actions_require_json(stack):
    local = stack["local"]
    code, _ = local.call("POST", "/local/attention", {"title": "x"}, headers={"Content-Type": "text/plain"})
    assert code == 415
    assert local.post("/local/attention", {"title": "Llamada", "show": False})["ok"]
    assert local.get("/local/status")["server_host"] == "127.0.0.1"


def test_single_instance_check_uses_the_chosen_port(stack, tmp_path):
    """Dos programas con puertos distintos no se confunden entre sí (antes el segundo se cerraba solo)."""
    from conftest import make_layout

    from devcontrol.desktop import App
    other = make_layout(tmp_path / "otro")
    busy_port = stack["agent"].local_port
    assert App(other, headless=True, port=busy_port).other_instance()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]
    assert not App(other, headless=True, port=free_port).other_instance()


def test_windows_notices_arrive_with_the_window_closed(stack):
    """2.2: el programa local avisa por Windows aunque la ventana esté cerrada, sin repetir y respetando preferencias."""
    from conftest import create_user

    from devcontrol.agent.notifier import in_quiet_hours, plan
    local, agent = stack["local"], stack["agent"]
    shown = []
    agent.hooks["toast"] = lambda title, body, link, urgent, tag: shown.append((title, body, link))
    pid = local.get("/api/me")["projects"][0]["id"]
    luis = create_user(local, "luis", "dev", project_ids=[pid])
    luis.base = local.base
    me = local.get("/api/me")["user"]["id"]
    local.post("/local/session", {"token": local.token, "user_id": me})
    assert local.get("/local/status")["notifier"]["active"]
    agent.notifier.poll_once()                 # primera vez: empieza desde ahora, sin avisos antiguos
    assert shown == []
    t = luis.post("/api/tasks", {"uid": uuid.uuid4().hex, "title": "Revisar el login", "project_id": pid})["item"]
    agent.notifier.poll_once()
    assert len(shown) == 1 and t["key"] in shown[0][0] and shown[0][2].startswith("devcontrol://aviso/")
    agent.notifier.poll_once()
    assert len(shown) == 1                     # nunca dos veces el mismo
    # con la ventana delante no sale el aviso de Windows (se ve dentro del programa)
    local.post("/local/presence", {"focused": True})
    luis.post("/api/tasks", {"uid": uuid.uuid4().hex, "title": "Otra", "project_id": pid})
    agent.notifier.poll_once()
    assert len(shown) == 1
    local.post("/local/presence", {"focused": False})
    # sin «Aviso de Windows» para ese tipo, tampoco
    local.ok("PUT", "/api/notifications/prefs", {"items": [{"type": "task_created", "in_app": True, "desktop": False}]})
    local.post("/local/session", {"token": local.token, "user_id": me})   # como hace la interfaz al guardar
    luis.post("/api/tasks", {"uid": uuid.uuid4().hex, "title": "Tercera", "project_id": pid})
    agent.notifier.poll_once()
    assert len(shown) == 1
    # pulsar el aviso abre ese aviso en la interfaz
    agent.request_open("aviso/7")
    assert local.post("/local/presence", {"focused": True})["open"] == "aviso/7"
    agent.request_open("../../etc")
    assert local.post("/local/presence", {"focused": True})["open"] == ""
    # cerrar sesión deja de avisar
    local.post("/local/session", {"token": ""})
    assert not local.get("/local/status")["notifier"]["active"]
    # agrupación del chat y horario de silencio
    msgs = [{"id": i, "type": "message_new", "title": "Luis en #general", "body": f"m{i}"} for i in (1, 2, 3)]
    assert [x["title"] for x in plan(msgs)] == ["3 mensajes nuevos en #general"]
    many = [{"id": i, "type": "comment", "title": f"Aviso {i}", "body": ""} for i in range(10)]
    assert len(plan(many)) == 4 and plan(many)[-1]["title"] == "7 avisos nuevos más"
    assert in_quiet_hours("23:30", "22:00", "07:30") and in_quiet_hours("06:00", "22:00", "07:30")
    assert not in_quiet_hours("12:00", "22:00", "07:30") and not in_quiet_hours("12:00", None, None)
