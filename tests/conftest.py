"""Infraestructura común: un servidor real en un puerto libre con los datos en una carpeta temporal."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid

import pytest

from devcontrol.config import load_config
from devcontrol.paths import Layout

ADMIN_PASSWORD = "Zorro-azul-42"


class Client:
    """Cliente HTTP mínimo para las pruebas."""

    def __init__(self, base: str, token: str | None = None, headers: dict | None = None):
        self.base = base
        self.token = token
        self.headers = headers or {}

    def call(self, method: str, path: str, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        for k, v in {**self.headers, **(headers or {})}.items():
            req.add_header(k, v)
        if self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        if method != "GET":
            req.add_header("Idempotency-Key", uuid.uuid4().hex)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                ctype = resp.headers.get("Content-Type", "")
                return resp.status, (json.loads(raw) if "json" in ctype and raw else raw)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw)
            except ValueError:
                return exc.code, raw

    def ok(self, method: str, path: str, body=None, status=(200, 201)):
        code, data = self.call(method, path, body)
        assert code in (status if isinstance(status, tuple) else (status,)), f"{method} {path} -> {code} {data}"
        return data

    def get(self, path):
        return self.ok("GET", path)

    def post(self, path, body=None):
        return self.ok("POST", path, body or {})

    def patch(self, path, body):
        return self.ok("PATCH", path, body)

    def login(self, username: str, password: str) -> Client:
        data = Client(self.base).ok("POST", "/api/login", {"username": username, "password": password})
        return Client(self.base, data["token"])


def make_layout(tmp_path, role="anfitrion", port=0) -> Layout:
    root = tmp_path / "home"
    layout = Layout(docs_root=root / "Documentos", local_root=root / "Local")
    layout.local_root.mkdir(parents=True, exist_ok=True)
    layout.install_file.write_text(f"[instalacion]\nrol = {role}\npuerto = {port or 8765}\niniciar_con_windows = no\n",
                                   encoding="utf-8")
    return layout.ensure()


@pytest.fixture
def layout(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVCONTROL_HOME", str(tmp_path / "home"))
    return make_layout(tmp_path)


@pytest.fixture
def server(layout):
    from devcontrol.server.main import ServerApp
    cfg = load_config(layout)
    app = ServerApp(cfg, layout)
    port = app.start(port=0, listen=["127.0.0.1"])
    app.base = f"http://127.0.0.1:{port}"
    yield app
    app.shutdown(backup=False)


@pytest.fixture
def admin(server):
    """Servidor configurado: devuelve el cliente del administrador (Ana)."""
    anon = Client(server.base)
    anon.ok("POST", "/api/setup", {"admin": {"username": "ana", "display_name": "Ana", "password": ADMIN_PASSWORD},
                                   "project_key": "APP", "project_name": "App"})
    return anon.login("ana", ADMIN_PASSWORD)


def roles(admin: Client) -> dict:
    return {r["key"]: r["id"] for r in admin.get("/api/admin/roles")["items"]}


def create_user(admin: Client, username: str, role_key: str, **extra) -> Client:
    pw = "Secreta-" + uuid.uuid4().hex[:8]
    admin.post("/api/admin/users", {"username": username, "display_name": username.title(), "password": pw,
                                    "role_id": roles(admin)[role_key], "must_change_password": False, **extra})
    return admin.login(username, pw)


def new_incident(client: Client, title="La app se cierra al abrir el perfil", **extra) -> dict:
    body = {"uid": uuid.uuid4().hex, "title": title, "description": "Detalle", "priority": "alta",
            "category": "errores", "steps": "1. Abrir", "app_version": "1.0", **extra}
    return client.ok("POST", "/api/incidents", body)["item"]


os.environ.setdefault("DEVCONTROL_TESTS", "1")


@pytest.fixture(autouse=True)
def fast_passwords(monkeypatch):
    """Las contraseñas de prueba usan menos iteraciones para que las pruebas sean rápidas."""
    from devcontrol.server import auth
    monkeypatch.setattr(auth, "PBKDF2_ITERATIONS", 2000)
