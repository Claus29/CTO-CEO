"""Acceso por Internet a través de un túnel de Cloudflare.

El anfitrión sigue en su PC; Cloudflare hace de puente desde una dirección propia (https://devcontrol.tudominio.com)
hasta http://127.0.0.1:<puerto>. Aquí se activa o desactiva ese acceso, se guarda la dirección para enseñarla al
equipo y se comprueba que la dirección lleva de verdad a este servidor.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

from .common import clean_text, log_activity
from .web import bad_request

_urlopen = urllib.request.urlopen   # sustituible en las pruebas
ADDRESS_RE = re.compile(r"^https://[A-Za-z0-9.-]{3,253}(:\d{1,5})?$")


def normalize_address(value: str) -> str:
    value = (value or "").strip().rstrip("/")
    if not value:
        return ""
    if not value.lower().startswith(("http://", "https://")):
        value = "https://" + value
    if not ADDRESS_RE.match(value):
        raise bad_request("La dirección debe ser como https://devcontrol.tudominio.com (sin rutas detrás).")
    return value.lower()


def cloudflare_code(raw: bytes) -> str:
    """Número de error de las páginas de Cloudflare («Error code 1033», «error code: 502»)."""
    m = re.search(rb"[Ee]rror(?: code)?:?\s*(\d{3,4})", raw[:20000] if raw else b"")
    return m.group(1).decode() if m else ""


def register(router, app):
    db = app.db

    def state() -> dict:
        return {"enabled": bool(db.get_setting("internet_access")), "address": db.get_setting("internet_address") or "",
                "port": getattr(app, "port", None) or app.cfg.server_port}

    @router.route("GET", "/api/internet", perm="sistema.ajustes")
    def get_internet(req):
        return state()

    @router.route("PUT", "/api/internet", perm="sistema.ajustes")
    def put_internet(req):
        data = req.json
        with db.tx() as c:
            if "address" in data:
                address = normalize_address(clean_text(data.get("address"), "dirección", 300))
                if address != (db.get_setting("internet_address") or ""):
                    db.put_setting(c, "internet_address", address)
                    log_activity(db, c, req.user, "settings", None, "internet_address", "changed",
                                 f"Cambió la dirección de acceso por Internet: {address or '(ninguna)'}")
            if "enabled" in data:
                enabled = bool(data["enabled"])
                if enabled != bool(db.get_setting("internet_access")):
                    db.put_setting(c, "internet_access", enabled)
                    log_activity(db, c, req.user, "settings", None, "internet_access", "changed",
                                 "Activó el acceso por Internet" if enabled else "Desactivó el acceso por Internet")
        app.settings_invalidate()
        return state()

    @router.route("POST", "/api/internet/test", perm="sistema.ajustes")
    def test_internet(req):
        """El anfitrión entra en su propia dirección pública, como lo haría un miembro desde fuera."""
        address = normalize_address(str(req.json.get("address") or db.get_setting("internet_address") or ""))
        if not address:
            raise bad_request("Escribe primero la dirección (por ejemplo https://devcontrol.tudominio.com).")
        request = urllib.request.Request(address + "/api/health", headers={"User-Agent": "DEV-CONTROL-comprobacion"})
        port = getattr(app, "port", None) or app.cfg.server_port
        service = f"http://127.0.0.1:{port} (con http, sin la «s»)"
        asked_at = time.time()
        status, info, raw = 0, {}, b""
        try:
            with _urlopen(request, timeout=15) as resp:
                status = resp.status
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                raw = exc.read() or b""
            except OSError:
                raw = b""
        except (urllib.error.URLError, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            text = str(reason)
            if "Name or service not known" in text or "getaddrinfo" in text or "nodename" in text \
                    or "11001" in text or "No such host" in text:
                msg = ("Esa dirección no existe todavía. Revisa en Cloudflare la «Published application» del túnel "
                       "(tras crearla puede tardar unos minutos).")
            else:
                msg = f"No se puede llegar a {address} ({text})."
            return {"ok": False, "address": address, "message": msg}
        try:
            info = json.loads(raw or b"{}")
        except ValueError:
            info = {}
        if not isinstance(info, dict):
            info = {}
        reached = getattr(app, "tunnel_seen_at", 0) >= asked_at   # la petición llegó a DEV CONTROL por el túnel
        code = cloudflare_code(raw)
        shown = f"respuesta {status}" + (f", error {code} de Cloudflare" if code and code != str(status) else "")
        if status == 403 and info.get("error") == "internet_desactivado":
            return {"ok": False, "tunnel_ok": True, "address": address,
                    "message": "El túnel funciona y llega a este equipo, pero el acceso por Internet está desactivado. "
                               "Marca «Permitir entrar por Internet» y pulsa Guardar."}
        if not reached and (status == 530 or code in ("1033", "1016")):
            return {"ok": False, "address": address,
                    "message": f"Cloudflare no encuentra el túnel conectado ({shown}). Comprueba en Windows (services.msc) "
                               "que el servicio «Cloudflared agent» está en marcha y que en Cloudflare el túnel aparece "
                               "como «Healthy». Si tienes varios túneles, la «Published application» debe estar en el que dice "
                               "«Healthy»."}
        if not reached and status in (502, 503, 504, 520, 521, 522, 523, 524, 525, 526):
            return {"ok": False, "address": address,
                    "message": f"El túnel está conectado, pero cloudflared no consigue entrar en DEV CONTROL ({shown}). "
                               f"En Cloudflare abre el túnel → «Published application» (o «Public hostname») → Edit y pon en "
                               f"Service URL: {service}. Lo más habitual es haber puesto https:// o el puerto 8080 que "
                               "Cloudflare propone de ejemplo."}
        if status != 200 or info.get("app") != "DEV CONTROL":
            return {"ok": False, "address": address,
                    "message": f"Esa dirección no lleva a DEV CONTROL ({shown}). En Cloudflare, la «Published application» "
                               f"(o «Public hostname») debe tener Service URL: {service}."}
        if info.get("epoch") != db.epoch:
            return {"ok": False, "address": address,
                    "message": "Esa dirección lleva a OTRO servidor de DEV CONTROL, no a este."}
        if not info.get("via_internet"):
            return {"ok": False, "address": address,
                    "message": "La dirección responde, pero no a través del túnel de Cloudflare."}
        return {"ok": True, "address": address,
                "message": f"Todo correcto: {address} lleva a este DEV CONTROL a través de Cloudflare. "
                           "Los demás solo tienen que poner esa dirección como servidor al iniciar sesión."}
