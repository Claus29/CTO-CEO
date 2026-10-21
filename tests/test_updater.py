"""Actualizaciones: firma obligatoria, reglas de actualización obligatoria, comprobación y vuelta atrás."""
import base64
import hashlib
import json
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from conftest import new_incident
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from devcontrol import APP_VERSION, updater
from devcontrol.config import load_config


@pytest.fixture
def keys(monkeypatch):
    key = Ed25519PrivateKey.generate()
    priv = base64.b64encode(key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                              serialization.NoEncryption())).decode()
    pub = base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,
                                                         serialization.PublicFormat.Raw)).decode()
    monkeypatch.setattr(updater, "PUBLIC_KEY_B64", pub)
    return priv


def manifest(version="9.9.9", **extra):
    return json.dumps({"app": "DEV CONTROL", "version": version, "notes": "Novedades",
                       "installer": {"name": "DEVCONTROL-Setup.exe", "url": "https://example.invalid/s.exe",
                                     "sha256": "00" * 32, "size": 10}, **extra}).encode()


def test_signature_is_required(keys):
    raw = manifest()
    sig = updater.sign_manifest(raw, keys)
    assert updater.verify_manifest(raw, sig)["version"] == "9.9.9"
    with pytest.raises(updater.UpdateError):
        updater.verify_manifest(raw.replace(b"9.9.9", b"9.9.8"), sig)     # manifiesto alterado
    other = Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                                       serialization.NoEncryption())
    with pytest.raises(updater.UpdateError):
        updater.verify_manifest(raw, updater.sign_manifest(raw, base64.b64encode(other).decode()))


def test_project_public_key_is_valid():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    Ed25519PublicKey.from_public_bytes(base64.b64decode(updater.PUBLIC_KEY_B64))


def fake_github(raw, sig, seen=None, status=None):
    def get(url, headers=None, timeout=20):
        if seen is not None:
            seen.append((url, dict(headers or {})))
        if status:
            raise urllib.error.HTTPError(url, status, "x", {}, None)
        if url.endswith("/releases/latest"):
            return json.dumps({"assets": [{"name": "manifest.json", "url": "m"}, {"name": "manifest.json.sig", "url": "s"},
                                          {"name": "DEVCONTROL-Setup.exe", "url": "i"}]}).encode()
        return raw if url == "m" else sig.encode()
    return get


def test_host_update_is_mandatory(layout, keys):
    cfg = load_config(layout)
    raw = manifest()
    up = updater.Updater(layout, cfg, http_get=fake_github(raw, updater.sign_manifest(raw, keys)))
    up.check(force=True)
    st = up.status()
    assert st["available"] and st["mandatory"] and st["latest"] == "9.9.9" and st["state"] == "idle"


def test_member_waits_for_host(layout, keys):
    cfg = load_config(layout)
    cfg.role = "miembro"
    raw = manifest()
    up = updater.Updater(layout, cfg, http_get=fake_github(raw, updater.sign_manifest(raw, keys)))
    up.check(force=True)
    up.note_server({"version": APP_VERSION})
    assert up.status()["available"] and not up.status()["mandatory"]   # el anfitrión aún no se ha actualizado
    up.note_server({"version": "9.9.9"})
    assert up.status()["mandatory"]
    up2 = updater.Updater(layout, cfg, http_get=fake_github(raw, updater.sign_manifest(raw, keys)))
    up2.note_server({"version": APP_VERSION}, {"min_version": "9.0.0"})  # el servidor responde 426
    assert up2.status()["mandatory"]


def test_bad_signature_never_offers_update(layout, keys):
    cfg = load_config(layout)
    raw = manifest()
    up = updater.Updater(layout, cfg, http_get=fake_github(raw, base64.b64encode(b"x" * 64).decode()))
    up.check(force=True)
    st = up.status()
    assert not st["available"] and not st["mandatory"] and st["state"] == "error"


def test_no_internet_never_blocks(layout):
    def offline(url, headers=None, timeout=20):
        raise OSError("sin red")
    up = updater.Updater(layout, load_config(layout), http_get=offline)
    up.check(force=True)
    st = up.status()
    assert not st["mandatory"] and st["state"] == "idle" and "No se puede comprobar" in st["error"]


def test_apply_refuses_outside_installed_program(layout):
    up = updater.Updater(layout, load_config(layout))
    with pytest.raises(RuntimeError):
        up.apply()


def test_verify_after_update_and_revert(layout, admin, server):
    new_incident(admin)
    info = admin.post("/api/backups", {})
    name = (info.get("item") or info)["name"]
    cfg = load_config(layout)
    server.shutdown(backup=False)
    updater.write_marker(layout, {"from": "1.0.0", "to": APP_VERSION, "backup": name,
                                  "counts": {"incidents": 1, "users": 1}})
    assert updater.verify_after_update(layout, cfg) == 0
    assert updater.read_marker(layout)["result"] == "ok"
    log = (layout.updates_dir / "registro.txt").read_text(encoding="utf-8")
    assert "OK: actualizado" in log and "1 incidencias" in log
    # datos perdidos tras actualizar -> la comprobación falla
    updater.write_marker(layout, {"from": "1.0.0", "to": APP_VERSION, "counts": {"incidents": 5}})
    assert updater.verify_after_update(layout, cfg) == 1
    # versión distinta de la esperada -> falla
    updater.write_marker(layout, {"from": "1.0.0", "to": "99.0.0"})
    assert updater.verify_after_update(layout, cfg) == 1
    # vuelta atrás: restaura la copia previa
    updater.write_marker(layout, {"from": "1.0.0", "to": "99.0.0", "backup": name})
    assert updater.revert_data(layout, cfg) == 0
    assert "Se ha vuelto a la versión" in (layout.updates_dir / "registro.txt").read_text(encoding="utf-8")


def test_update_script_quotes_paths(layout, tmp_path):
    script = updater.build_script(tmp_path / "a'b" / "setup.exe", tmp_path / "Programa", tmp_path / "Anterior", layout, 1234)
    assert "'a''b'" not in script and "a''b" in script
    assert "--verificar-actualizacion" in script and "--revertir-datos" in script and "/ACTUALIZACION=1" in script


def test_private_repository_needs_token(layout, keys):
    cfg = load_config(layout)
    up = updater.Updater(layout, cfg, hooks={"predownload": False}, http_get=fake_github(b"", "", status=404))
    up.check(force=True)
    assert "clave de acceso" in up.status()["error"] and not up.status()["token_configured"]
    seen = []
    raw = manifest()
    up = updater.Updater(layout, cfg, hooks={"predownload": False}, http_get=fake_github(raw, updater.sign_manifest(raw, keys), seen))
    up.set_token("github_pat_prueba123")
    assert up.status()["token_configured"] and b"github_pat" not in up.token_file.read_bytes()[:6]
    up.check(force=True)
    assert up.status()["available"] and up.installer_url == "i"
    assert all(h.get("Authorization") == "Bearer github_pat_prueba123" for _, h in seen)
    # el anfitrión guarda la versión firmada para servírsela a los demás equipos
    assert (layout.updates_dir / "manifest.json").read_bytes() == raw
    up.set_token("")
    assert not up.status()["token_configured"]


def test_members_update_from_the_host(layout, keys, admin, server):
    installer = b"instalador de prueba " * 1000
    raw = json.dumps({"app": "DEV CONTROL", "version": "9.9.9", "notes": "",
                      "installer": {"name": "DEVCONTROL-Setup-9.9.9.exe", "url": "https://x/y.exe",
                                    "sha256": hashlib.sha256(installer).hexdigest(), "size": len(installer)}}).encode()
    layout.updates_dir.mkdir(parents=True, exist_ok=True)
    (layout.updates_dir / "manifest.json").write_bytes(raw)
    (layout.updates_dir / "manifest.json.sig").write_text(updater.sign_manifest(raw, keys))
    cfg = load_config(layout)
    cfg.role = "miembro"
    member_layout = type(layout)(layout.docs_root.parent / "M-docs", layout.local_root.parent / "M-local").ensure()
    up = updater.Updater(member_layout, cfg, hooks={"server_url": lambda: server.base})
    # el anfitrión aún no tiene el instalador: aviso claro
    up.check(force=True)
    assert up.status()["latest"] == "9.9.9" and up.status()["source"] == "anfitrion"
    with pytest.raises(updater.UpdateError, match="todavía no tiene el instalador"):
        up._download(up.latest["installer"])
    (layout.updates_dir / "DEVCONTROL-Setup-9.9.9.exe").write_bytes(installer)
    path = up._download(up.latest["installer"], quiet=True)
    assert path.read_bytes() == installer and path.parent == member_layout.updates_dir
    # una firma alterada en el anfitrión se rechaza
    (layout.updates_dir / "manifest.json").write_bytes(raw.replace(b"9.9.9", b"9.9.8"))
    up.check(force=True)
    assert up.status()["state"] == "error" and "firma" in up.status()["error"]


def test_token_never_sent_to_other_hosts():
    received = {}

    class Target(BaseHTTPRequestHandler):
        def do_GET(self):
            received["auth"] = self.headers.get("Authorization")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    target = HTTPServer(("127.0.0.1", 0), Target)
    port_b = target.server_address[1]

    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):
            received["first_auth"] = self.headers.get("Authorization")
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{port_b}/archivo")
            self.end_headers()

        def log_message(self, *a):
            pass

    origin = HTTPServer(("127.0.0.1", 0), Redirect)
    for srv in (target, origin):
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        body = updater._http_get(f"http://127.0.0.1:{origin.server_address[1]}/asset", {"Authorization": "Bearer x"})
        assert body == b"ok" and received["first_auth"] == "Bearer x" and received["auth"] is None
    finally:
        target.shutdown()
        origin.shutdown()
