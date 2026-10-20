"""Configuración inicial, sesiones, verificación en dos pasos, versión mínima y modo recuperación."""
import time

from conftest import ADMIN_PASSWORD, Client, create_user, roles

from devcontrol import MIN_CLIENT_VERSION
from devcontrol.server import auth


def test_health_and_setup(server):
    anon = Client(server.base)
    health = anon.get("/api/health")
    assert health["app"] == "DEV CONTROL" and health["setup_required"] and not health["recovery"]
    assert health["min_client_version"] == MIN_CLIENT_VERSION
    code, err = anon.call("POST", "/api/setup", {"admin": {"username": "ana", "display_name": "Ana", "password": "corta"}})
    assert code == 400
    anon.post("/api/setup", {"admin": {"username": "ana", "display_name": "Ana", "password": ADMIN_PASSWORD},
                             "project_key": "APP", "project_name": "App"})
    code, _ = anon.call("POST", "/api/setup", {"admin": {"username": "otro", "display_name": "Otro",
                                                         "password": ADMIN_PASSWORD}})
    assert code == 409
    me = anon.login("ana", ADMIN_PASSWORD).get("/api/me")
    assert me["user"]["role_key"] == "admin"
    assert "sistema.usuarios" in me["permissions"]
    assert [p["key"] for p in me["projects"]] == ["APP"]


def test_login_errors_and_throttle(admin):
    anon = Client(admin.base)
    code, err = anon.call("POST", "/api/login", {"username": "ana", "password": "mal"})
    assert code == 401 and err["error"] == "credenciales_incorrectas"
    for _ in range(10):
        code, err = anon.call("POST", "/api/login", {"username": "nadie", "password": "x"})
    assert code == 429


def test_unauthenticated_and_logout(admin):
    assert Client(admin.base).call("GET", "/api/me")[0] == 401
    admin.post("/api/logout")
    assert admin.call("GET", "/api/me")[0] == 401


def test_old_client_must_update(admin):
    code, err = admin.call("GET", "/api/me", headers={"X-DevControl-Version": "1.9.0"})
    assert code == 426 and err["error"] == "actualizacion_obligatoria" and err["min_version"] == MIN_CLIENT_VERSION
    assert admin.call("GET", "/api/me", headers={"X-DevControl-Version": MIN_CLIENT_VERSION})[0] == 200


def test_must_change_password_gate(admin):
    admin.post("/api/admin/users", {"username": "luis", "display_name": "Luis", "password": "Temporal-1234",
                                    "role_id": roles(admin)["dev"], "must_change_password": True})
    luis = admin.login("luis", "Temporal-1234")
    assert luis.get("/api/me")["gate"]["code"] == "debe_cambiar_contrasena"
    code, err = luis.call("GET", "/api/incidents")
    assert code == 403 and err["error"] == "debe_cambiar_contrasena"
    luis.post("/api/me/password", {"current": "Temporal-1234", "new_password": "Nueva-clave-987"})
    assert luis.get("/api/me")["gate"] is None
    assert luis.call("GET", "/api/incidents")[0] == 200


def test_two_factor_login_and_recovery_code(admin):
    start = admin.post("/api/me/2fa/start")
    code = auth.totp_code(start["secret"])
    assert admin.call("POST", "/api/me/2fa/confirm", {"code": "000000"})[0] == 400
    codes = admin.post("/api/me/2fa/confirm", {"code": code})["recovery_codes"]
    anon = Client(admin.base)
    st, err = anon.call("POST", "/api/login", {"username": "ana", "password": ADMIN_PASSWORD})
    assert st == 401 and err["error"] == "requiere_2fa"
    st, err = anon.call("POST", "/api/login", {"username": "ana", "password": ADMIN_PASSWORD, "totp_code": "123456"})
    assert st == 401 and err["error"] == "codigo_2fa_incorrecto"
    ok = anon.ok("POST", "/api/login", {"username": "ana", "password": ADMIN_PASSWORD,
                                        "totp_code": auth.totp_code(start["secret"], time.time())})
    assert ok["token"]
    ok = anon.ok("POST", "/api/login", {"username": "ana", "password": ADMIN_PASSWORD, "recovery_code": codes[0]})
    assert ok["token"]
    st, _ = anon.call("POST", "/api/login", {"username": "ana", "password": ADMIN_PASSWORD, "recovery_code": codes[0]})
    assert st == 401  # cada código sirve una sola vez


def test_required_2fa_for_role(admin):
    dev = create_user(admin, "dani", "dev")
    settings = admin.get("/api/settings")
    assert "require_2fa_roles" in str(settings)
    admin.ok("PUT", "/api/settings", {"require_2fa_roles": ["dev"]})
    assert dev.get("/api/me")["gate"]["code"] == "debe_activar_2fa"
    assert dev.call("GET", "/api/tasks")[0] == 403


def test_sessions_list_and_close(admin):
    other = admin.login("ana", ADMIN_PASSWORD)
    items = admin.get("/api/me/sessions")["items"]
    assert len(items) == 2 and sum(1 for s in items if s["current"]) == 1
    other_id = next(s["id"] for s in items if not s["current"])
    admin.ok("DELETE", f"/api/me/sessions/{other_id}")
    assert other.call("GET", "/api/me")[0] == 401
    assert admin.call("GET", "/api/me")[0] == 200


def test_setup_only_from_host(server, monkeypatch):
    monkeypatch.setattr(server.cfg, "is_host_itself", lambda ip: False)
    code, err = Client(server.base).call("POST", "/api/setup", {"admin": {"username": "ana", "display_name": "Ana",
                                                                          "password": ADMIN_PASSWORD}})
    assert code == 403


def test_rejects_public_networks(server):
    assert server.cfg.is_allowed_client("127.0.0.1")
    assert not server.cfg.is_allowed_client("203.0.113.9")


def test_changes_long_poll(admin):
    first = admin.get("/api/changes?since=0&wait=0")
    seq = first["seq"]
    create_user(admin, "eva", "qa")
    nxt = admin.get(f"/api/changes?since={seq}&wait=2&epoch={first['epoch']}")
    assert nxt["seq"] > seq and any(c["kind"] == "user" for c in nxt["changes"])
