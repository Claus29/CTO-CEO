"""Programa local: proxy, cola sin conexión con reenvío sin duplicados, caché y elección de servidor."""
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
