"""Configuración de este equipo: Documentos\\DEV CONTROL\\Configuración\\config.ini.

Se crea la primera vez a partir de los valores elegidos en el instalador.
Se puede editar con el Bloc de notas; los comentarios se conservan al guardar
cambios desde el programa.
"""
from __future__ import annotations

import configparser
import ipaddress
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import DEFAULT_REPOSITORY
from .paths import Layout, default_layout

DEFAULT_SERVER_PORT = 8765
DEFAULT_LOCAL_PORT = 8770
DEFAULT_CALL_PORT = 8766
PRIVATE_NETWORKS = ("100.64.0.0/10", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fd00::/8",
                    "fd7a:115c:a1e0::/48")
DEFAULT_BLOCKED_EXTENSIONS = "exe, bat, cmd, com, scr, msi, ps1, vbs, vbe, jse, wsf, jar, reg, lnk, hta, cpl"

TEMPLATE = """; =====================================================================
;  DEV CONTROL - Configuración de este equipo
;  Edita con el Bloc de notas. Las líneas que empiezan por ; son comentarios.
;  Reinicia DEV CONTROL después de cambiar algo.
; =====================================================================

[general]
; anfitrion = este equipo guarda los datos y ejecuta el servidor del equipo
; miembro   = este equipo se conecta al servidor de un anfitrión
rol = {rol}

[servidor]
; Solo se usa si este equipo es el anfitrión.
puerto = {puerto}
; Puerto del repetidor de llamadas (UDP y TCP). Los audios y vídeos que no pueden ir directos
; entre equipos pasan por aquí. Rango de puertos que usa para reenviar (UDP).
puerto_llamadas = {puerto_llamadas}
puertos_reenvio = 50000-50199
; Direcciones en las que escuchar, separadas por comas. "auto" = todas las
; redes privadas de este equipo (Tailscale, VPN, red local). Nunca Internet.
escuchar = auto
; Redes desde las que se aceptan conexiones (solo rangos privados).
redes_permitidas = {redes}
; Lista opcional de equipos concretos permitidos (IP separadas por comas).
; Vacío = cualquier equipo de las redes permitidas.
equipos_permitidos =
; Carpeta de la base de datos y los adjuntos. Vacío = ubicación por defecto
; (AppData). No uses una carpeta sincronizada con OneDrive.
carpeta_datos =
tamano_maximo_adjunto_mb = 200
extensiones_bloqueadas = {bloqueadas}

[cliente]
; Servidor predeterminado al que se conecta este equipo (IP, nombre o IP:puerto).
; En el anfitrión se deja vacío: se conecta a sí mismo.
servidor = {servidor}
; Puerto interno de la interfaz (solo accesible desde este equipo).
puerto_local = {puerto_local}

[actualizaciones]
; Repositorio de GitHub del que el anfitrión descarga las actualizaciones (los demás equipos las reciben de él).
repositorio = {repo}
; Cada cuántos minutos se busca una versión nueva (mínimo 10).
comprobar_cada_minutos = 30
; si = las versiones nuevas se instalan solas (con un aviso de 2 minutos y nunca durante una llamada).
automaticas = si

[escritorio]
iniciar_con_windows = {autostart}
minimizar_al_cerrar = si
; Solo en el anfitrión: si = el equipo no se suspende mientras DEV CONTROL esté abierto (la pantalla sí se apaga).
mantener_despierto = si
"""


class ConfigError(Exception):
    """Error de configuración con un mensaje comprensible."""


def parse_server_address(value: str, default_port: int = DEFAULT_SERVER_PORT) -> tuple[str, int]:
    """'100.1.2.3', 'pc-claudio', '100.1.2.3:9000' o '[fd7a::1]:9000' -> (host, puerto)."""
    value = (value or "").strip()
    if not value:
        raise ConfigError("Indica la dirección del servidor.")
    m = re.match(r"^\[([0-9a-fA-F:]+)\](?::(\d+))?$", value)
    if m:
        host, port = m.group(1), m.group(2)
    elif value.count(":") == 1:
        host, port = value.split(":")
    elif value.count(":") > 1:
        host, port = value, None  # IPv6 sin puerto
    else:
        host, port = value, None
    host = host.strip()
    if not re.match(r"^[A-Za-z0-9._:-]{1,253}$", host):
        raise ConfigError(f"La dirección «{value}» no es válida.")
    try:
        port_n = int(port) if port else default_port
    except ValueError:
        raise ConfigError(f"El puerto de «{value}» no es un número.") from None
    if not 1 <= port_n <= 65535:
        raise ConfigError("El puerto debe estar entre 1 y 65535.")
    return host, port_n


def parse_server_url(value: str, default_port: int = DEFAULT_SERVER_PORT) -> tuple[str, str, int]:
    """Como parse_server_address, pero admite también 'https://devcontrol.midominio.com' (túnel por Internet).

    Devuelve (esquema, host, puerto). Sin esquema se usa http y el puerto de DEV CONTROL; con https,
    el 443 salvo que se indique otro."""
    value = (value or "").strip()
    m = re.match(r"^(https?)://", value, re.IGNORECASE)
    if not m:
        return ("http", *parse_server_address(value, default_port))
    scheme = m.group(1).lower()
    rest = value[m.end():].rstrip("/")
    if "/" in rest or "?" in rest or "#" in rest:
        raise ConfigError(f"La dirección «{value}» no debe llevar ruta: deja solo https://nombre.dominio")
    host, port = parse_server_address(rest, 443 if scheme == "https" else 80)
    return scheme, host, port


def format_server_address(host: str, port: int, scheme: str = "http") -> str:
    h = f"[{host}]" if ":" in host else host
    if scheme == "https":
        return f"https://{h}" if port == 443 else f"https://{h}:{port}"
    return h if port == DEFAULT_SERVER_PORT else f"{h}:{port}"


def server_base_url(scheme: str, host: str, port: int) -> str:
    h = f"[{host}]" if ":" in host else host
    default = 443 if scheme == "https" else 80
    return f"{scheme}://{h}" if port == default else f"{scheme}://{h}:{port}"


@dataclass
class Config:
    path: Path | None = None
    layout: Layout | None = None
    role: str = "miembro"
    server_port: int = DEFAULT_SERVER_PORT
    call_port: int = DEFAULT_CALL_PORT
    relay_ports: tuple = (50000, 50199)
    listen: list = field(default_factory=lambda: ["auto"])
    allowed_networks: list = field(default_factory=lambda: list(PRIVATE_NETWORKS))
    allowed_clients: list = field(default_factory=list)
    data_dir: Path | None = None
    max_upload_mb: int = 200
    blocked_extensions: set = field(default_factory=lambda: {
        e.strip() for e in DEFAULT_BLOCKED_EXTENSIONS.split(",")})
    default_server: str = ""
    local_port: int = DEFAULT_LOCAL_PORT
    update_repo: str = DEFAULT_REPOSITORY
    update_every_minutes: int = 30
    auto_update: bool = True
    autostart: bool = True
    minimize_on_close: bool = True
    keep_awake: bool = True
    # IP propias del anfitrión (las que recibe el servidor de sí mismo)
    own_addresses: set = field(default_factory=set)

    @property
    def is_host(self) -> bool:
        return self.role == "anfitrion"

    @property
    def server_data_dir(self) -> Path:
        if self.data_dir:
            return self.data_dir
        return (self.layout or default_layout()).server_dir

    def default_server_url(self) -> tuple[str, str, int]:
        """(esquema, host, puerto) del servidor por defecto de este equipo."""
        if self.is_host and not self.default_server:
            return "http", "127.0.0.1", self.server_port
        return parse_server_url(self.default_server or "127.0.0.1", self.server_port)

    def default_server_address(self) -> tuple[str, int]:
        return self.default_server_url()[1:]

    # -- red ----------------------------------------------------------------
    @staticmethod
    def _addr(ip: str):
        try:
            addr = ipaddress.ip_address(ip.split("%")[0])
        except ValueError:
            return None
        if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
            addr = addr.ipv4_mapped
        return addr

    def is_host_itself(self, ip: str) -> bool:
        addr = self._addr(ip)
        return bool(addr) and (addr.is_loopback or str(addr) in self.own_addresses)

    def is_allowed_client(self, ip: str) -> bool:
        """Solo el propio equipo y equipos de las redes privadas permitidas."""
        addr = self._addr(ip)
        if addr is None:
            return False
        if addr.is_loopback or str(addr) in self.own_addresses:
            return True
        if not any(addr in ipaddress.ip_network(n) for n in self.allowed_networks if _net_ok(n)):
            return False
        return not self.allowed_clients or str(addr) in self.allowed_clients


def _net_ok(n: str) -> bool:
    try:
        ipaddress.ip_network(n)
        return True
    except ValueError:
        return False


def validate_private_network(text: str) -> str:
    try:
        net = ipaddress.ip_network(text.strip(), strict=False)
    except ValueError:
        raise ConfigError(f"«{text}» no es una red válida (ejemplo: 100.64.0.0/10).") from None
    shared = net.version == 4 and net.subnet_of(ipaddress.ip_network("100.64.0.0/10"))
    if not (net.is_private or shared):
        raise ConfigError(f"La red {text} es pública. DEV CONTROL nunca se expone a Internet.")
    return str(net)


def _yes(value: str) -> bool:
    return value.strip().lower() in ("si", "sí", "1", "true", "yes", "on")


def ensure_config_file(layout: Layout) -> Path:
    """Crea config.ini la primera vez, con los valores del instalador si existen."""
    layout.ensure()
    path = layout.config_file
    install = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    if layout.install_file.exists():
        install.read(layout.install_file, encoding="utf-8-sig")
    values = {
        "rol": install.get("instalacion", "rol", fallback="miembro"),
        "puerto": install.get("instalacion", "puerto", fallback=str(DEFAULT_SERVER_PORT)),
        "puerto_llamadas": install.get("instalacion", "puerto_llamadas", fallback=str(DEFAULT_CALL_PORT)),
        "servidor": install.get("instalacion", "servidor", fallback=""),
        "autostart": "si" if _yes(install.get("instalacion", "iniciar_con_windows", fallback="si")) else "no",
        "puerto_local": str(DEFAULT_LOCAL_PORT),
        "redes": ", ".join(PRIVATE_NETWORKS),
        "bloqueadas": DEFAULT_BLOCKED_EXTENSIONS,
        "repo": DEFAULT_REPOSITORY,
    }
    if not path.exists():
        path.write_text(TEMPLATE.format(**values), encoding="utf-8")
        if layout.install_file.exists():
            set_values(layout.install_file, {("instalacion", "aplicar"): "no"})
    elif layout.install_file.exists() and _yes(install.get("instalacion", "aplicar", fallback="no")):
        # Una instalación interactiva (no una actualización) puede cambiar el rol o el servidor.
        set_values(path, {("general", "rol"): values["rol"], ("servidor", "puerto"): values["puerto"],
                          ("cliente", "servidor"): values["servidor"],
                          ("escritorio", "iniciar_con_windows"): values["autostart"]})
        set_values(layout.install_file, {("instalacion", "aplicar"): "no"})
    return path


def load_config(layout: Layout | None = None, path: Path | None = None) -> Config:
    layout = layout or default_layout()
    path = Path(path) if path else ensure_config_file(layout)
    cp = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    try:
        cp.read(path, encoding="utf-8-sig")
    except configparser.Error as exc:
        raise ConfigError(f"El archivo {path} tiene un error de formato: {exc}") from None

    def get(section, key, default=""):
        return cp.get(section, key, fallback=default).strip()

    def get_int(section, key, default):
        raw = get(section, key, str(default))
        try:
            return int(raw)
        except ValueError:
            raise ConfigError(f"[{section}] {key} debe ser un número (ahora: «{raw}»).") from None

    cfg = Config(path=path, layout=layout)
    cfg.role = get("general", "rol", "miembro").lower().replace("í", "i")
    if cfg.role not in ("anfitrion", "miembro"):
        raise ConfigError("[general] rol debe ser «anfitrion» o «miembro».")
    cfg.server_port = get_int("servidor", "puerto", DEFAULT_SERVER_PORT)
    cfg.call_port = get_int("servidor", "puerto_llamadas", DEFAULT_CALL_PORT)
    raw_range = get("servidor", "puertos_reenvio", "50000-50199")
    try:
        lo, hi = (int(x) for x in raw_range.split("-"))
        if not 1024 <= lo < hi <= 65535:
            raise ValueError
    except ValueError:
        raise ConfigError("[servidor] puertos_reenvio debe ser un rango como 50000-50199.") from None
    cfg.relay_ports = (lo, hi)
    cfg.listen = [x.strip() for x in get("servidor", "escuchar", "auto").split(",") if x.strip()] or ["auto"]
    for addr in cfg.listen:
        if addr != "auto":
            ip = Config._addr(addr)
            if ip is None:
                raise ConfigError(f"[servidor] escuchar: «{addr}» no es una IP válida.")
            if not (ip.is_private or ip.is_loopback or ip in ipaddress.ip_network("100.64.0.0/10")):
                raise ConfigError(f"[servidor] escuchar: {addr} es una IP pública. DEV CONTROL nunca se expone "
                                  "a Internet; usa la IP de tu red privada (Tailscale, VPN o red local).")
    nets = get("servidor", "redes_permitidas", ", ".join(PRIVATE_NETWORKS))
    cfg.allowed_networks = [validate_private_network(n) for n in nets.split(",") if n.strip()]
    cfg.allowed_clients = [x.strip() for x in get("servidor", "equipos_permitidos").split(",") if x.strip()]
    data_dir = get("servidor", "carpeta_datos")
    cfg.data_dir = Path(data_dir) if data_dir else None
    cfg.max_upload_mb = get_int("servidor", "tamano_maximo_adjunto_mb", 200)
    exts = get("servidor", "extensiones_bloqueadas", DEFAULT_BLOCKED_EXTENSIONS)
    cfg.blocked_extensions = {e.strip().lower().lstrip(".") for e in exts.split(",") if e.strip()}
    cfg.default_server = get("cliente", "servidor")
    if cfg.default_server:
        parse_server_url(cfg.default_server, cfg.server_port)
    cfg.local_port = get_int("cliente", "puerto_local", DEFAULT_LOCAL_PORT)
    cfg.update_repo = get("actualizaciones", "repositorio", DEFAULT_REPOSITORY) or DEFAULT_REPOSITORY
    cfg.update_every_minutes = max(10, get_int("actualizaciones", "comprobar_cada_minutos", 30))
    cfg.auto_update = _yes(get("actualizaciones", "automaticas", "si"))
    cfg.autostart = _yes(get("escritorio", "iniciar_con_windows", "si"))
    cfg.minimize_on_close = _yes(get("escritorio", "minimizar_al_cerrar", "si"))
    cfg.keep_awake = _yes(get("escritorio", "mantener_despierto", "si"))
    for port, label in ((cfg.server_port, "[servidor] puerto"), (cfg.local_port, "[cliente] puerto_local")):
        if not 1024 <= port <= 65535:
            raise ConfigError(f"{label} debe estar entre 1024 y 65535.")
    if len({cfg.server_port, cfg.local_port, cfg.call_port}) < 3:
        raise ConfigError("[servidor] puerto, [servidor] puerto_llamadas y [cliente] puerto_local deben ser distintos.")
    return cfg


def set_values(path: Path, values: dict) -> None:
    """Cambia claves {(sección, clave): valor} conservando comentarios; añade las que falten."""
    path = Path(path)
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    pending = {(s.lower(), k.lower()): v for (s, k), v in values.items()}
    out: list[str] = []
    section = None

    def flush_section(sec):
        for (s, k), v in list(pending.items()):
            if s == sec:
                out.append(f"{k} = {v}")
                del pending[(s, k)]

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if section is not None:
                flush_section(section)
            section = stripped[1:-1].strip().lower()
        elif section and "=" in stripped and not stripped.startswith((";", "#")):
            key = stripped.split("=", 1)[0].strip().lower()
            if (section, key) in pending:
                line = f"{key} = {pending.pop((section, key))}"
        out.append(line)
    if section is not None:
        flush_section(section)
    for s in sorted({s for s, _ in pending}):
        out.append(f"\n[{s}]")
        flush_section(s)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
