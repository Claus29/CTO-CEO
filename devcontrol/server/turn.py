"""Repetidor de llamadas (TURN, RFC 5766/8656) integrado en el anfitrión, solo con la biblioteca estándar.

Las llamadas intentan primero ir directas entre los equipos. Si no pueden verse (redes distintas, cortafuegos…),
el navegador envía el audio y el vídeo a este repetidor, que los reenvía al otro participante. Así una llamada
conecta siempre que los dos puedan usar DEV CONTROL. El contenido va cifrado de extremo a extremo (DTLS-SRTP): el
repetidor solo mueve paquetes y no puede escucharlos.

Seguridad:
  * Solo atiende a las redes privadas permitidas en la configuración (igual que el servidor).
  * Credenciales temporales por llamada: usuario «caducidad:usuario:llamada» y contraseña HMAC firmada por el servidor;
    al reservar se comprueba además que la llamada sigue activa y que el usuario participa en ella.
  * Los puertos de reenvío salen de un rango fijo (por defecto 50000–50199) para poder abrirlos en el firewall.
"""
from __future__ import annotations

import base64
import collections
import hashlib
import hmac
import os
import secrets
import selectors
import socket
import struct
import threading
import time
import zlib
from dataclasses import dataclass, field

MAGIC = 0x2112A442
REALM = "devcontrol"
SOFTWARE = b"DEV CONTROL"

# métodos
BINDING, ALLOCATE, REFRESH, SEND, DATA, CREATE_PERMISSION, CHANNEL_BIND = 0x001, 0x003, 0x004, 0x006, 0x007, 0x008, 0x009
# clases
REQUEST, INDICATION, SUCCESS, ERROR = 0x0000, 0x0010, 0x0100, 0x0110
# atributos
A_USERNAME, A_MESSAGE_INTEGRITY, A_ERROR_CODE, A_CHANNEL_NUMBER = 0x0006, 0x0008, 0x0009, 0x000C
A_LIFETIME, A_XOR_PEER, A_DATA, A_REALM, A_NONCE, A_XOR_RELAYED = 0x000D, 0x0012, 0x0013, 0x0014, 0x0015, 0x0016
A_REQUESTED_TRANSPORT, A_XOR_MAPPED, A_SOFTWARE, A_FINGERPRINT = 0x0019, 0x0020, 0x8022, 0x8028

DEFAULT_LIFETIME = 600
MAX_LIFETIME = 3600
PERMISSION_LIFETIME = 300
CHANNEL_LIFETIME = 600
NONCE_SECONDS = 600
MAX_ALLOCATIONS_PER_USER = 16


def msg_type(method: int, cls: int) -> int:
    return (method & 0x000F) | ((method & 0x0070) << 1) | ((method & 0x0F80) << 2) | cls


def split_type(t: int) -> tuple[int, int]:
    cls = t & 0x0110
    method = (t & 0x000F) | ((t >> 1) & 0x0070) | ((t >> 2) & 0x0F80)
    return method, cls


def xor_address(ip: str, port: int, tid: bytes) -> bytes:
    """Dirección XOR (solo IPv4)."""
    packed = socket.inet_aton(ip)
    xip = bytes(a ^ b for a, b in zip(packed, struct.pack(">I", MAGIC), strict=False))
    return struct.pack(">BBH", 0, 0x01, port ^ (MAGIC >> 16)) + xip


def parse_xor_address(value: bytes, tid: bytes) -> tuple[str, int] | None:
    if len(value) < 8 or value[1] != 0x01:
        return None  # IPv6 no admitido
    port = struct.unpack(">H", value[2:4])[0] ^ (MAGIC >> 16)
    ip = socket.inet_ntoa(bytes(a ^ b for a, b in zip(value[4:8], struct.pack(">I", MAGIC), strict=False)))
    return ip, port


@dataclass
class Message:
    method: int
    cls: int
    tid: bytes
    attrs: list = field(default_factory=list)   # [(tipo, valor)]
    raw: bytes = b""

    def get(self, attr: int) -> bytes | None:
        for t, v in self.attrs:
            if t == attr:
                return v
        return None

    def all(self, attr: int) -> list[bytes]:
        return [v for t, v in self.attrs if t == attr]


def parse(data: bytes) -> Message | None:
    if len(data) < 20 or data[0] & 0xC0:
        return None
    t, length, cookie = struct.unpack(">HHI", data[:8])
    if cookie != MAGIC or length + 20 > len(data) or length % 4:
        return None
    method, cls = split_type(t)
    msg = Message(method, cls, data[8:20], raw=data[:20 + length])
    pos = 20
    while pos + 4 <= 20 + length:
        at, al = struct.unpack(">HH", data[pos:pos + 4])
        msg.attrs.append((at, data[pos + 4:pos + 4 + al]))
        pos += 4 + al + (-al % 4)
    return msg


def build(method: int, cls: int, tid: bytes, attrs: list, key: bytes | None = None, fingerprint: bool = True) -> bytes:
    body = b""
    for at, val in attrs:
        body += struct.pack(">HH", at, len(val)) + val + b"\x00" * (-len(val) % 4)
    t = msg_type(method, cls)
    if key is not None:
        head = struct.pack(">HHI", t, len(body) + 24, MAGIC) + tid
        mac = hmac.new(key, head + body, hashlib.sha1).digest()
        body += struct.pack(">HH", A_MESSAGE_INTEGRITY, 20) + mac
    if fingerprint:
        head = struct.pack(">HHI", t, len(body) + 8, MAGIC) + tid
        crc = (zlib.crc32(head + body) ^ 0x5354554E) & 0xFFFFFFFF
        body += struct.pack(">HHI", A_FINGERPRINT, 4, crc)
    return struct.pack(">HHI", t, len(body), MAGIC) + tid + body


def check_integrity(msg: Message, key: bytes) -> bool:
    raw = msg.raw
    pos = 20
    while pos + 4 <= len(raw):
        at, al = struct.unpack(">HH", raw[pos:pos + 4])
        if at == A_MESSAGE_INTEGRITY:
            head = raw[:2] + struct.pack(">H", pos - 20 + 24) + raw[4:20]
            expected = hmac.new(key, head + raw[20:pos], hashlib.sha1).digest()
            return hmac.compare_digest(expected, raw[pos + 4:pos + 24])
        pos += 4 + al + (-al % 4)
    return False


def long_term_key(username: str, password: str) -> bytes:
    return hashlib.md5(f"{username}:{REALM}:{password}".encode()).digest()  # noqa: S324 - lo exige el estándar TURN


def make_credentials(secret: bytes, user_id: int, call_id: int, ttl: int = 6 * 3600) -> tuple[str, str]:
    username = f"{int(time.time()) + ttl}:{user_id}:{call_id}"
    password = base64.b64encode(hmac.new(secret, username.encode(), hashlib.sha1).digest()).decode()
    return username, password


@dataclass
class Allocation:
    key: tuple
    user_id: int
    username: str
    hkey: bytes
    relay: socket.socket
    relay_ip: str
    client: tuple             # (ip, puerto) del cliente
    transport: object         # "udp" o la conexión TCP
    server_sock: socket.socket | None
    expires: float
    tid: bytes
    permissions: dict = field(default_factory=dict)     # ip -> caduca
    channels: dict = field(default_factory=dict)        # número -> (peer, caduca)
    peer_channels: dict = field(default_factory=dict)   # peer -> número


class TcpConn:
    def __init__(self, sock: socket.socket, addr):
        self.sock = sock
        self.addr = addr
        self.buf = b""
        self.out = b""


class TurnServer:
    """Servidor TURN en un único hilo (selectors)."""

    def __init__(self, port: int, secret: bytes, allowed_client, authorize, port_range=(50000, 50199), log=None):
        self.port = port
        self.secret = secret
        self.allowed_client = allowed_client      # ip -> bool (redes privadas permitidas)
        self.authorize = authorize                # (user_id, call_id) -> bool (llamada activa y participante)
        self.port_range = port_range
        self.log = log
        self.sel = selectors.DefaultSelector()
        self.allocs: dict[tuple, Allocation] = {}
        self.by_relay: dict[socket.socket, Allocation] = {}
        self.nonce_key = secrets.token_bytes(16)
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.udp: socket.socket | None = None
        self.tcp: socket.socket | None = None
        self.error = ""
        self._drops: collections.deque = collections.deque()

    # -- arranque y parada ----------------------------------------------------------
    def start(self) -> None:
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.bind(("0.0.0.0", self.port))
        self.udp.setblocking(False)
        self.port = self.udp.getsockname()[1]
        self.tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name != "nt":
            self.tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.tcp.bind(("0.0.0.0", self.port))
        self.tcp.listen(32)
        self.tcp.setblocking(False)
        self.sel.register(self.udp, selectors.EVENT_READ, ("udp", None))
        self.sel.register(self.tcp, selectors.EVENT_READ, ("listen", None))
        self.thread = threading.Thread(target=self._loop, name="repetidor-llamadas", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)
        for alloc in list(self.allocs.values()):
            self._free(alloc)
        for s in (self.udp, self.tcp):
            if s:
                try:
                    s.close()
                except OSError:
                    pass

    def stats(self) -> dict:
        return {"port": self.port, "allocations": len(self.allocs), "error": self.error}

    def drop_call(self, call_id: int) -> None:
        """Libera las reservas de una llamada terminada (se llama desde otro hilo; se hace en el siguiente ciclo)."""
        self._drops.append(call_id)

    # -- bucle principal ------------------------------------------------------------------
    def _loop(self) -> None:
        last_sweep = 0.0
        while not self.stop_event.is_set():
            try:
                events = self.sel.select(timeout=0.5)
            except OSError:
                events = []
            for key, mask in events:
                kind, extra = key.data
                try:
                    if kind == "udp":
                        self._read_udp(key.fileobj)
                    elif kind == "listen":
                        self._accept()
                    elif kind == "tcp":
                        self._read_tcp(extra, mask)
                    elif kind == "relay":
                        self._read_relay(key.fileobj)
                except Exception as exc:  # un paquete malo nunca debe parar el repetidor
                    if self.log:
                        self.log.debug("Repetidor: error procesando paquete: %s", exc)
            now = time.monotonic()
            if now - last_sweep >= 1:
                last_sweep = now
                self._sweep(now)

    def _sweep(self, now: float) -> None:
        drop = set()
        while self._drops:
            drop.add(self._drops.popleft())
        for alloc in list(self.allocs.values()):
            call_id = int(alloc.username.split(":")[2])
            if alloc.expires < now or call_id in drop:
                self._free(alloc)
                continue
            alloc.permissions = {ip: exp for ip, exp in alloc.permissions.items() if exp > now}
            for num, (peer, exp) in list(alloc.channels.items()):
                if exp < now:
                    del alloc.channels[num]
                    alloc.peer_channels.pop(peer, None)

    def _free(self, alloc: Allocation) -> None:
        self.allocs.pop(alloc.key, None)
        self.by_relay.pop(alloc.relay, None)
        try:
            self.sel.unregister(alloc.relay)
        except (KeyError, ValueError):
            pass
        try:
            alloc.relay.close()
        except OSError:
            pass

    # -- transporte ----------------------------------------------------------------------------
    def _read_udp(self, sock: socket.socket) -> None:
        for _ in range(64):
            try:
                data, addr = sock.recvfrom(65535)
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                return  # p. ej. ICMP «puerto inalcanzable» en Windows
            if not self.allowed_client(addr[0]):
                continue
            self._handle(data, addr, "udp", sock)

    def _accept(self) -> None:
        try:
            conn, addr = self.tcp.accept()
        except OSError:
            return
        if not self.allowed_client(addr[0]):
            conn.close()
            return
        conn.setblocking(False)
        tc = TcpConn(conn, addr)
        self.sel.register(conn, selectors.EVENT_READ, ("tcp", tc))

    def _read_tcp(self, tc: TcpConn, mask: int) -> None:
        if mask & selectors.EVENT_WRITE and tc.out:
            self._flush(tc)
        if not mask & selectors.EVENT_READ:
            return
        try:
            chunk = tc.sock.recv(65535)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            chunk = b""
        if not chunk:
            self._close_tcp(tc)
            return
        tc.buf += chunk
        while len(tc.buf) >= 4:
            first = tc.buf[0]
            if 0x40 <= first <= 0x7F:   # ChannelData (relleno a múltiplos de 4 en TCP)
                length = struct.unpack(">H", tc.buf[2:4])[0]
                total = 4 + length + (-length % 4)
            else:
                if len(tc.buf) < 20:
                    break
                total = 20 + struct.unpack(">H", tc.buf[2:4])[0]
            if len(tc.buf) < total:
                break
            frame, tc.buf = tc.buf[:total], tc.buf[total:]
            self._handle(frame, tc.addr, tc, tc.sock)

    def _close_tcp(self, tc: TcpConn) -> None:
        try:
            self.sel.unregister(tc.sock)
        except (KeyError, ValueError):
            pass
        tc.sock.close()
        for alloc in [a for a in self.allocs.values() if a.transport is tc]:
            self._free(alloc)

    def _flush(self, tc: TcpConn) -> None:
        try:
            sent = tc.sock.send(tc.out)
            tc.out = tc.out[sent:]
        except (BlockingIOError, InterruptedError):
            pass
        except OSError:
            self._close_tcp(tc)
            return
        try:
            self.sel.modify(tc.sock, selectors.EVENT_READ | (selectors.EVENT_WRITE if tc.out else 0), ("tcp", tc))
        except (KeyError, ValueError):
            pass

    def _send(self, transport, server_sock, addr, data: bytes) -> None:
        if transport == "udp":
            try:
                server_sock.sendto(data, addr)
            except OSError:
                pass
        else:
            if len(transport.out) > 4_000_000:   # cliente que no lee: se descarta antes que bloquear
                return
            transport.out += data
            self._flush(transport)

    # -- mensajes del cliente ---------------------------------------------------------------------
    def _five_tuple(self, transport, addr, server_sock) -> tuple:
        if transport == "udp":
            return ("udp", addr, server_sock.getsockname())
        return ("tcp", id(transport))

    def _handle(self, data: bytes, addr, transport, server_sock) -> None:
        if 0x40 <= data[0] <= 0x7F:   # ChannelData del cliente hacia un participante
            alloc = self.allocs.get(self._five_tuple(transport, addr, server_sock))
            if not alloc or len(data) < 4:
                return
            num, length = struct.unpack(">HH", data[:4])
            ch = alloc.channels.get(num)
            if ch:
                try:
                    alloc.relay.sendto(data[4:4 + length], ch[0])
                except OSError:
                    pass
            return
        msg = parse(data)
        if not msg:
            return
        reply = lambda m, c, attrs, key=None: self._send(transport, server_sock, addr,  # noqa: E731
                                                          build(m, c, msg.tid, attrs, key))
        if msg.cls == REQUEST and msg.method == BINDING:
            reply(BINDING, SUCCESS, [(A_XOR_MAPPED, xor_address(addr[0], addr[1], msg.tid)), (A_SOFTWARE, SOFTWARE)])
            return
        five = self._five_tuple(transport, addr, server_sock)
        alloc = self.allocs.get(five)
        if msg.cls == INDICATION and msg.method == SEND:
            peer_raw, payload = msg.get(A_XOR_PEER), msg.get(A_DATA)
            peer = parse_xor_address(peer_raw, msg.tid) if peer_raw else None
            if alloc and peer and payload is not None and peer[0] in alloc.permissions:
                try:
                    alloc.relay.sendto(payload, peer)
                except OSError:
                    pass
            return
        if msg.cls != REQUEST:
            return
        # Autenticación (credenciales de larga duración)
        user = (msg.get(A_USERNAME) or b"").decode("utf-8", "replace")
        nonce = (msg.get(A_NONCE) or b"").decode("ascii", "replace")
        challenge = [(A_REALM, REALM.encode()), (A_NONCE, self._nonce().encode())]
        if not user or msg.get(A_MESSAGE_INTEGRITY) is None:
            reply(msg.method, ERROR, [(A_ERROR_CODE, self._err(401, "Unauthorized"))] + challenge)
            return
        if not self._nonce_ok(nonce):
            reply(msg.method, ERROR, [(A_ERROR_CODE, self._err(438, "Stale Nonce"))] + challenge)
            return
        ident = self._credentials(user)
        if not ident:
            reply(msg.method, ERROR, [(A_ERROR_CODE, self._err(401, "Unauthorized"))] + challenge)
            return
        user_id, call_id, password = ident
        key = long_term_key(user, password)
        if not check_integrity(msg, key):
            reply(msg.method, ERROR, [(A_ERROR_CODE, self._err(401, "Unauthorized"))] + challenge)
            return

        if msg.method == ALLOCATE:
            if alloc:
                if alloc.tid == msg.tid:   # retransmisión
                    reply(ALLOCATE, SUCCESS, self._alloc_attrs(alloc, addr, msg.tid), key)
                else:
                    reply(ALLOCATE, ERROR, [(A_ERROR_CODE, self._err(437, "Allocation Mismatch"))], key)
                return
            transport_attr = msg.get(A_REQUESTED_TRANSPORT)
            if not transport_attr or transport_attr[0] != 17:
                reply(ALLOCATE, ERROR, [(A_ERROR_CODE, self._err(442, "Unsupported Transport Protocol"))], key)
                return
            if not self.authorize(user_id, call_id):
                reply(ALLOCATE, ERROR, [(A_ERROR_CODE, self._err(403, "Forbidden"))], key)
                return
            if sum(1 for a in self.allocs.values() if a.user_id == user_id) >= MAX_ALLOCATIONS_PER_USER:
                reply(ALLOCATE, ERROR, [(A_ERROR_CODE, self._err(486, "Allocation Quota Reached"))], key)
                return
            relay_ip = self._local_ip_for(addr[0], transport, server_sock)
            relay = self._open_relay(relay_ip)
            if relay is None:
                reply(ALLOCATE, ERROR, [(A_ERROR_CODE, self._err(508, "Insufficient Capacity"))], key)
                return
            alloc = Allocation(key=five, user_id=user_id, username=user, hkey=key, relay=relay, relay_ip=relay_ip,
                               client=addr, transport=transport, server_sock=server_sock,
                               expires=time.monotonic() + self._lifetime(msg, DEFAULT_LIFETIME), tid=msg.tid)
            self.allocs[five] = alloc
            self.by_relay[relay] = alloc
            self.sel.register(relay, selectors.EVENT_READ, ("relay", None))
            reply(ALLOCATE, SUCCESS, self._alloc_attrs(alloc, addr, msg.tid), key)
            return
        if not alloc or alloc.username != user:
            reply(msg.method, ERROR, [(A_ERROR_CODE, self._err(437, "Allocation Mismatch"))], key)
            return
        if msg.method == REFRESH:
            lifetime = self._lifetime(msg, DEFAULT_LIFETIME)
            if lifetime == 0:
                self._free(alloc)
            else:
                alloc.expires = time.monotonic() + lifetime
            reply(REFRESH, SUCCESS, [(A_LIFETIME, struct.pack(">I", lifetime))], key)
        elif msg.method == CREATE_PERMISSION:
            peers = [parse_xor_address(v, msg.tid) for v in msg.all(A_XOR_PEER)]
            if not peers or any(p is None for p in peers):
                reply(msg.method, ERROR, [(A_ERROR_CODE, self._err(400, "Bad Request"))], key)
                return
            for ip, _ in peers:
                alloc.permissions[ip] = time.monotonic() + PERMISSION_LIFETIME
            reply(CREATE_PERMISSION, SUCCESS, [], key)
        elif msg.method == CHANNEL_BIND:
            num_raw, peer_raw = msg.get(A_CHANNEL_NUMBER), msg.get(A_XOR_PEER)
            peer = parse_xor_address(peer_raw, msg.tid) if peer_raw else None
            num = struct.unpack(">H", num_raw[:2])[0] if num_raw and len(num_raw) >= 2 else 0
            current = alloc.channels.get(num)
            if not peer or not 0x4000 <= num <= 0x7FFE or (current and current[0] != peer) or \
                    alloc.peer_channels.get(peer, num) != num:
                reply(CHANNEL_BIND, ERROR, [(A_ERROR_CODE, self._err(400, "Bad Request"))], key)
                return
            alloc.channels[num] = (peer, time.monotonic() + CHANNEL_LIFETIME)
            alloc.peer_channels[peer] = num
            alloc.permissions[peer[0]] = time.monotonic() + PERMISSION_LIFETIME
            reply(CHANNEL_BIND, SUCCESS, [], key)
        else:
            reply(msg.method, ERROR, [(A_ERROR_CODE, self._err(400, "Bad Request"))], key)

    # -- datos de los participantes hacia el cliente ------------------------------------------------
    def _read_relay(self, relay: socket.socket) -> None:
        alloc = self.by_relay.get(relay)
        for _ in range(64):
            try:
                data, peer = relay.recvfrom(65535)
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                return
            if alloc is None or peer[0] not in alloc.permissions:
                continue
            num = alloc.peer_channels.get(peer)
            if num is not None:
                frame = struct.pack(">HH", num, len(data)) + data
                if alloc.transport != "udp":
                    frame += b"\x00" * (-len(data) % 4)
            else:
                frame = build(DATA, INDICATION, secrets.token_bytes(12),
                              [(A_XOR_PEER, xor_address(peer[0], peer[1], b"")), (A_DATA, data)], fingerprint=False)
            self._send(alloc.transport, alloc.server_sock, alloc.client, frame)

    # -- utilidades ------------------------------------------------------------------------------------
    def _alloc_attrs(self, alloc: Allocation, addr, tid: bytes) -> list:
        remaining = max(0, int(alloc.expires - time.monotonic()))
        return [(A_XOR_RELAYED, xor_address(alloc.relay_ip, alloc.relay.getsockname()[1], tid)),
                (A_XOR_MAPPED, xor_address(addr[0], addr[1], tid)),
                (A_LIFETIME, struct.pack(">I", remaining)), (A_SOFTWARE, SOFTWARE)]

    @staticmethod
    def _lifetime(msg: Message, default: int) -> int:
        raw = msg.get(A_LIFETIME)
        if raw is None or len(raw) < 4:
            return default
        value = struct.unpack(">I", raw[:4])[0]
        return 0 if value == 0 else max(60, min(value, MAX_LIFETIME))

    @staticmethod
    def _err(code: int, reason: str) -> bytes:
        return struct.pack(">HBB", 0, code // 100, code % 100) + reason.encode()

    def _nonce(self) -> str:
        ts = str(int(time.time()))
        mac = hmac.new(self.nonce_key, ts.encode(), hashlib.sha256).hexdigest()[:16]
        return f"{ts}-{mac}"

    def _nonce_ok(self, nonce: str) -> bool:
        try:
            ts, mac = nonce.split("-", 1)
            ok = hmac.compare_digest(mac, hmac.new(self.nonce_key, ts.encode(), hashlib.sha256).hexdigest()[:16])
            return ok and time.time() - int(ts) < NONCE_SECONDS
        except ValueError:
            return False

    def _credentials(self, username: str) -> tuple[int, int, str] | None:
        try:
            expiry, user_id, call_id = username.split(":")
            if int(expiry) < time.time():
                return None
            password = base64.b64encode(hmac.new(self.secret, username.encode(), hashlib.sha1).digest()).decode()
            return int(user_id), int(call_id), password
        except ValueError:
            return None

    @staticmethod
    def _local_ip_for(client_ip: str, transport, server_sock) -> str:
        """IP de este equipo por la que llega ese cliente (la que verán los participantes)."""
        if transport != "udp":
            return transport.sock.getsockname()[0]
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((client_ip, 9))
                return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"

    def _open_relay(self, ip: str) -> socket.socket | None:
        lo, hi = self.port_range
        start = secrets.randbelow(hi - lo + 1)
        # Si Windows tiene reservado todo el rango (Hyper-V, WSL o Docker reservan bloques de puertos al azar),
        # se usa un puerto libre cualquiera: la regla del Firewall para el programa también lo cubre.
        ports = [lo + (start + i) % (hi - lo + 1) for i in range(hi - lo + 1)] + [0]
        for port in ports:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                # La reserva se ata a la IP anunciada: así los paquetes salen con esa IP de origen y los
                # permisos del otro participante coinciden aunque los dos usen el repetidor.
                s.bind((ip, port))
            except OSError:
                s.close()
                continue
            s.setblocking(False)
            return s
        return None
