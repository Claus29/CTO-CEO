"""2.5: acceso por Internet con un túnel de Cloudflare, conexión https desde los miembros y copia espejo."""
import http.client
import io
import urllib.error
import urllib.request

import pytest
from conftest import ADMIN_PASSWORD, Client, create_user

from devcontrol.agent import main as agent_main
from devcontrol.config import ConfigError, format_server_address, parse_server_url, server_base_url
from devcontrol.server.web import tunnel_origin


def tunnel(server, ip="203.0.113.7", token=None) -> Client:
    """Cliente que llega como lo haría cloudflared: desde 127.0.0.1 y con la IP real en CF-Connecting-IP."""
    return Client(server.base, token, {"CF-Connecting-IP": ip})


def enable_internet(admin, enabled=True):
    return admin.ok("PUT", "/api/internet", {"enabled": enabled, "address": "devcontrol.ejemplo.com"})


# ---------- origen de la petición ----------
def test_tunnel_origin_only_trusts_the_header_from_loopback():
    assert tunnel_origin("127.0.0.1", {"cf-connecting-ip": "198.51.100.4"}) == ("198.51.100.4", True)
    assert tunnel_origin("::1", {"cf-connecting-ip": "2001:db8::1"}) == ("2001:db8::1", True)
    assert tunnel_origin("127.0.0.1", {}) == ("127.0.0.1", False)
    # desde otro equipo la cabecera no se cree: podría falsificarla cualquiera de la red
    assert tunnel_origin("100.64.0.5", {"cf-connecting-ip": "127.0.0.1"}) == ("100.64.0.5", False)
    # una cabecera rara nunca se convierte en una IP de confianza
    assert tunnel_origin("127.0.0.1", {"cf-connecting-ip": "basura"}) == ("0.0.0.0", True)


def test_internet_is_off_until_the_host_enables_it(admin, server):
    code, data = tunnel(server).call("GET", "/api/health")
    assert code == 403 and data["error"] == "internet_desactivado"
    assert Client(server.base).get("/api/health")["via_internet"] is False      # en local todo sigue igual
    enable_internet(admin)
    health = tunnel(server).get("/api/health")
    assert health["via_internet"] is True and health["app"] == "DEV CONTROL"
    # en Internet no se enseña el nombre del equipo ni sus IP, ni se ofrecen las llamadas
    assert "server_name" not in health and "listening" not in health and "call_port" not in health
    assert "calls" not in health["features"] and health["max_upload_mb"] <= 95
    enable_internet(admin, False)
    assert tunnel(server).call("GET", "/api/health")[0] == 403


def test_login_through_the_tunnel_uses_the_real_ip(admin, server):
    enable_internet(admin)
    data = tunnel(server, "198.51.100.20").ok("POST", "/api/login", {"username": "ana", "password": ADMIN_PASSWORD})
    me = tunnel(server, "198.51.100.20", data["token"])
    assert me.get("/api/me")["user"]["username"] == "ana"
    ips = {s["client_ip"] for s in admin.get("/api/me/sessions")["items"]}
    assert "198.51.100.20" in ips


def test_failed_logins_are_blocked_per_real_ip(admin, server):
    enable_internet(admin)
    attacker = tunnel(server, "198.51.100.66")
    for _ in range(8):
        assert attacker.call("POST", "/api/login", {"username": "ana", "password": "mala"})[0] == 401
    assert attacker.call("POST", "/api/login", {"username": "ana", "password": "mala"})[0] == 429
    # otra persona que entra por el mismo túnel no queda bloqueada, ni tampoco la red local
    ok = tunnel(server, "198.51.100.67").call("POST", "/api/login", {"username": "ana", "password": ADMIN_PASSWORD})
    assert ok[0] == 200
    assert Client(server.base).call("POST", "/api/login", {"username": "ana", "password": ADMIN_PASSWORD})[0] == 200


def test_host_only_actions_are_refused_through_the_tunnel(admin, server):
    enable_internet(admin)
    code, _ = tunnel(server).call("POST", "/internal/shutdown", {}, {"X-DevControl-Local-Secret": server.local_secret})
    assert code == 403


def test_internet_settings_need_permission_and_a_clean_address(admin, server):
    st = enable_internet(admin)
    assert st == {"enabled": True, "address": "https://devcontrol.ejemplo.com", "port": server.port}
    for bad in ("https://devcontrol.ejemplo.com/ruta", "ftp://x", "https://a b.com"):
        assert admin.call("PUT", "/api/internet", {"address": bad})[0] == 400
    pid = admin.get("/api/me")["projects"][0]["id"]
    luis = create_user(admin, "luis", "dev", project_ids=[pid])
    assert luis.call("GET", "/api/internet")[0] == 403
    assert luis.call("PUT", "/api/internet", {"enabled": False})[0] == 403


def test_check_button_explains_each_situation(admin, server, monkeypatch):
    from devcontrol.server import internet

    real_urlopen = urllib.request.urlopen

    def via_cloudflare(request, timeout=None):
        # Simula Cloudflare: la dirección pública llega a este servidor por 127.0.0.1 con la IP real.
        url = request.full_url.replace("https://devcontrol.ejemplo.com", server.base)
        return real_urlopen(urllib.request.Request(url, headers={"CF-Connecting-IP": "198.51.100.9"}), timeout=timeout)

    monkeypatch.setattr(internet, "_urlopen", via_cloudflare)
    admin.ok("PUT", "/api/internet", {"address": "https://devcontrol.ejemplo.com"})
    r = admin.post("/api/internet/test")
    assert r["ok"] is False and r["tunnel_ok"] is True and "desactivado" in r["message"]
    enable_internet(admin)
    r = admin.post("/api/internet/test")
    assert r["ok"] is True and "Todo correcto" in r["message"]

    def cloudflare_down(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 530, "Origin DNS error", {}, io.BytesIO(b"<html>error 1033</html>"))

    monkeypatch.setattr(internet, "_urlopen", cloudflare_down)
    r = admin.post("/api/internet/test")
    assert r["ok"] is False and "no encuentra el túnel conectado" in r["message"] and "1033" in r["message"]

    def origin_unreachable(request, timeout=None):
        # El túnel está «Healthy» pero la Public hostname apunta a otro sitio (HTTPS, puerto 8080...): cloudflared da 502.
        page = b"<html><title>Bad gateway</title><span>Error code 502</span> Host Error</html>"
        raise urllib.error.HTTPError(request.full_url, 502, "Bad Gateway", {}, io.BytesIO(page))

    monkeypatch.setattr(internet, "_urlopen", origin_unreachable)
    r = admin.post("/api/internet/test")
    assert r["ok"] is False and "no consigue entrar en DEV CONTROL" in r["message"]
    assert f"Service URL: http://127.0.0.1:{server.port} (con http, sin la «s»)" in r["message"]


# ---------- miembro: direcciones https ----------
def test_server_urls_accept_https_addresses():
    assert parse_server_url("100.64.0.9") == ("http", "100.64.0.9", 8765)
    assert parse_server_url("pc-claudio:9000") == ("http", "pc-claudio", 9000)
    assert parse_server_url("https://devcontrol.ejemplo.com") == ("https", "devcontrol.ejemplo.com", 443)
    assert parse_server_url("HTTPS://devcontrol.ejemplo.com/") == ("https", "devcontrol.ejemplo.com", 443)
    assert parse_server_url("https://dc.ejemplo.com:8443") == ("https", "dc.ejemplo.com", 8443)
    assert parse_server_url("http://10.0.0.5") == ("http", "10.0.0.5", 80)
    with pytest.raises(ConfigError):
        parse_server_url("https://devcontrol.ejemplo.com/api")
    assert format_server_address("devcontrol.ejemplo.com", 443, "https") == "https://devcontrol.ejemplo.com"
    assert format_server_address("10.0.0.5", 8765) == "10.0.0.5"
    assert server_base_url("https", "devcontrol.ejemplo.com", 443) == "https://devcontrol.ejemplo.com"
    assert server_base_url("http", "10.0.0.5", 8765) == "http://10.0.0.5:8765"


def test_agent_uses_tls_for_https_servers():
    assert isinstance(agent_main.open_connection("https", "devcontrol.ejemplo.com", 443, 5), http.client.HTTPSConnection)
    plain = agent_main.open_connection("http", "10.0.0.5", 8765, 5)
    assert type(plain) is http.client.HTTPConnection


class FakeResp:
    def __init__(self, status, ctype):
        self.status, self._ctype = status, ctype

    def getheader(self, name, default=None):
        return self._ctype if name.lower() == "content-type" else default


def test_agent_understands_cloudflare_answers():
    # Cloudflare responde por el anfitrión apagado (HTML): es como no tener conexión, y se explica.
    assert agent_main.tunnel_down(FakeResp(530, "text/html"))
    assert agent_main.tunnel_down(FakeResp(502, "text/html; charset=UTF-8"))
    assert not agent_main.tunnel_down(FakeResp(503, "application/json"))   # un error de DEV CONTROL de verdad
    assert not agent_main.tunnel_down(FakeResp(200, "text/html"))
    assert "apagado" in agent_main.describe_bad_answer(530, b"<html></html>")
    msg = agent_main.describe_bad_answer(403, b'{"error": "internet_desactivado", "message": "El anfitri\\u00f3n no permite"}')
    assert msg.startswith("El anfitrión no permite")
    hint = agent_main.describe_network_error(TimeoutError(), "devcontrol.ejemplo.com", 8765, False)
    assert "https://devcontrol.ejemplo.com" in hint
    assert "https://" not in agent_main.describe_network_error(TimeoutError(), "100.64.0.9", 8765, False)


def test_agent_connects_to_an_https_address(tmp_path, monkeypatch):
    from conftest import make_layout

    from devcontrol.config import load_config

    monkeypatch.setenv("DEVCONTROL_HOME", str(tmp_path / "home"))
    layout = make_layout(tmp_path, role="miembro")
    agent = agent_main.Agent(load_config(layout), layout)
    seen = []

    def fake_send(method, path, headers, body=None, body_file=None, timeout=30, host=None, port=None, scheme=None):
        seen.append((scheme or agent.server_scheme, host or agent.server_host, port or agent.server_port))
        raise ConnectionRefusedError()

    monkeypatch.setattr(agent, "send", fake_send)
    status = agent.connect("https://devcontrol.ejemplo.com")
    assert seen[-1] == ("https", "devcontrol.ejemplo.com", 443)
    assert status["server_label"] == "https://devcontrol.ejemplo.com" and status["server_scheme"] == "https"
    assert agent.server_key == "https://devcontrol.ejemplo.com:443"
    agent.connect("100.64.0.9")
    assert agent.server_key == "100.64.0.9:8765" and agent.server_scheme == "http"   # la clave de siempre
    agent.outbox.close()


# ---------- copia espejo ----------
def test_backups_are_mirrored_to_a_cloud_folder(admin, server, tmp_path):
    mirror = tmp_path / "OneDrive" / "Copias DEV CONTROL"
    st = admin.ok("PUT", "/api/backups/mirror", {"folder": str(mirror)})
    assert st["folder"] == str(mirror) and mirror.is_dir()
    assert "backup_mirror_dir" not in admin.get("/api/settings")["settings"]   # la ruta del anfitrión no se reparte
    item = admin.post("/api/backups")["item"]
    assert item["mirror"]["ok"] is True and (mirror / item["name"]).exists()
    assert not list(mirror.glob("*.parcial"))
    st = admin.get("/api/backups/mirror")
    assert st["status"]["ok"] is True and st["status"]["name"] == item["name"]
    # «Copiar ahora» crea una copia nueva y también la deja en la carpeta espejo
    r = admin.post("/api/backups/mirror/now")
    assert (mirror / r["item"]["name"]).exists()
    assert admin.call("PUT", "/api/backups/mirror", {"folder": "relativa/carpeta"})[0] == 400
    assert admin.call("PUT", "/api/backups/mirror", {"folder": str(server.backups.dir)})[0] == 400
    admin.ok("PUT", "/api/backups/mirror", {"folder": ""})
    assert "mirror" not in admin.post("/api/backups")["item"]


def test_mirror_failure_never_breaks_the_backup(admin, server, tmp_path):
    blocker = tmp_path / "no-es-carpeta"
    admin.ok("PUT", "/api/backups/mirror", {"folder": str(tmp_path / "espejo")})
    blocker.write_text("x")
    with server.db.tx() as c:
        server.db.put_setting(c, "backup_mirror_dir", str(blocker / "dentro"))
    item = admin.post("/api/backups")["item"]
    assert (server.backups.dir / item["name"]).exists()
    assert item["mirror"]["ok"] is False and "No se pudo copiar" in item["mirror"]["error"]


def test_mirror_keeps_only_recent_copies(server, admin, tmp_path):
    mirror = tmp_path / "espejo"
    admin.ok("PUT", "/api/backups/mirror", {"folder": str(mirror)})
    admin.ok("PUT", "/api/settings", {"backup_keep": 2})
    for i in range(4):
        (mirror / f"devcontrol-copia-2020010{i + 1}-000000-auto.zip").write_bytes(b"x")
    for i in range(12):
        (mirror / f"devcontrol-copia-2020020{i % 9 + 1}-0000{i:02d}-manual.zip").write_bytes(b"x")
    admin.post("/api/backups")
    names = sorted(p.name for p in mirror.glob("*.zip"))
    assert len([n for n in names if n.endswith("-auto.zip")]) == 2
    assert len([n for n in names if not n.endswith("-auto.zip")]) == 10
