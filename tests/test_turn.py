"""Repetidor de llamadas: autenticación, reservas, permisos y reenvío entre dos participantes."""
import socket
import struct
import time

import pytest

from devcontrol.server import turn


class TurnClient:
    def __init__(self, port, username, password):
        self.addr = ("127.0.0.1", port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(2)
        self.username, self.password = username, password
        self.key = None
        self.realm = self.nonce = None

    def request(self, method, attrs, auth=True):
        tid = turn.secrets.token_bytes(12)
        full = list(attrs)
        key = None
        if auth and self.nonce:
            full += [(turn.A_USERNAME, self.username.encode()), (turn.A_REALM, self.realm), (turn.A_NONCE, self.nonce)]
            key = turn.long_term_key(self.username, self.password)
        self.sock.sendto(turn.build(method, turn.REQUEST, tid, full, key), self.addr)
        while True:
            msg = turn.parse(self.sock.recv(65535))
            if msg and msg.tid == tid:
                return msg

    def allocate(self):
        first = self.request(turn.ALLOCATE, [(turn.A_REQUESTED_TRANSPORT, b"\x11\x00\x00\x00")], auth=False)
        assert first.cls == turn.ERROR and struct.unpack(">BB", first.get(turn.A_ERROR_CODE)[2:4]) == (4, 1)
        self.realm, self.nonce = first.get(turn.A_REALM), first.get(turn.A_NONCE)
        resp = self.request(turn.ALLOCATE, [(turn.A_REQUESTED_TRANSPORT, b"\x11\x00\x00\x00")])
        return resp

    def relayed(self, resp):
        return turn.parse_xor_address(resp.get(turn.A_XOR_RELAYED), resp.tid)


@pytest.fixture
def server():
    authorized = {(7, 1), (8, 1)}
    srv = turn.TurnServer(0, b"secreto-de-prueba", lambda ip: True, lambda u, c: (u, c) in authorized,
                          port_range=(51000, 51050))
    srv.start()
    yield srv
    srv.stop()


def client(server, user_id, call_id=1, secret=b"secreto-de-prueba"):
    u, p = turn.make_credentials(secret, user_id, call_id)
    return TurnClient(server.port, u, p)


def test_binding_without_auth(server):
    c = client(server, 7)
    resp = c.request(turn.BINDING, [], auth=False)
    assert resp.cls == turn.SUCCESS
    assert turn.parse_xor_address(resp.get(turn.A_XOR_MAPPED), resp.tid)[0] == "127.0.0.1"


def test_wrong_credentials_and_unauthorized_call(server):
    bad = client(server, 7, secret=b"otro-secreto")
    first = bad.request(turn.ALLOCATE, [(turn.A_REQUESTED_TRANSPORT, b"\x11\x00\x00\x00")], auth=False)
    bad.realm, bad.nonce = first.get(turn.A_REALM), first.get(turn.A_NONCE)
    resp = bad.request(turn.ALLOCATE, [(turn.A_REQUESTED_TRANSPORT, b"\x11\x00\x00\x00")])
    assert resp.cls == turn.ERROR
    outsider = client(server, 9)                      # no participa en la llamada 1
    resp = outsider.allocate()
    assert resp.cls == turn.ERROR and resp.get(turn.A_ERROR_CODE)[3] == 3   # 403


def test_relay_between_two_participants(server):
    a, b = client(server, 7), client(server, 8)
    ra, rb = a.relayed(a.allocate()), b.relayed(b.allocate())
    assert ra[1] != rb[1]
    tid = turn.secrets.token_bytes(12)
    # sin permiso, nada pasa
    a.sock.sendto(turn.build(turn.SEND, turn.INDICATION, tid, [(turn.A_XOR_PEER, turn.xor_address(*rb, tid)),
                                                               (turn.A_DATA, b"hola")]), a.addr)
    assert a.request(turn.CREATE_PERMISSION, [(turn.A_XOR_PEER, turn.xor_address(rb[0], 0, b""))]).cls == turn.SUCCESS
    assert b.request(turn.CREATE_PERMISSION, [(turn.A_XOR_PEER, turn.xor_address(ra[0], 0, b""))]).cls == turn.SUCCESS
    # Send indication: A -> repetidor A -> repetidor B -> B (indicación Data)
    tid = turn.secrets.token_bytes(12)
    a.sock.sendto(turn.build(turn.SEND, turn.INDICATION, tid, [(turn.A_XOR_PEER, turn.xor_address(*rb, tid)),
                                                               (turn.A_DATA, b"audio-1")]), a.addr)
    msg = turn.parse(b.sock.recv(65535))
    assert msg.method == turn.DATA and msg.get(turn.A_DATA) == b"audio-1"
    assert turn.parse_xor_address(msg.get(turn.A_XOR_PEER), msg.tid) == ra
    # ChannelData en sentido contrario
    assert b.request(turn.CHANNEL_BIND, [(turn.A_CHANNEL_NUMBER, struct.pack(">HH", 0x4001, 0)),
                                          (turn.A_XOR_PEER, turn.xor_address(*ra, b""))]).cls == turn.SUCCESS
    assert a.request(turn.CHANNEL_BIND, [(turn.A_CHANNEL_NUMBER, struct.pack(">HH", 0x4002, 0)),
                                          (turn.A_XOR_PEER, turn.xor_address(*rb, b""))]).cls == turn.SUCCESS
    b.sock.sendto(struct.pack(">HH", 0x4001, 7) + b"video-2", b.addr)
    frame = a.sock.recv(65535)
    assert struct.unpack(">H", frame[:2])[0] == 0x4002 and frame[4:11] == b"video-2"


def test_refresh_zero_frees_and_drop_call(server):
    a = client(server, 7)
    a.allocate()
    assert server.stats()["allocations"] == 1
    assert a.request(turn.REFRESH, [(turn.A_LIFETIME, struct.pack(">I", 0))]).cls == turn.SUCCESS
    time.sleep(0.1)
    assert server.stats()["allocations"] == 0
    b = client(server, 8)
    b.allocate()
    server.drop_call(1)
    time.sleep(1.6)
    assert server.stats()["allocations"] == 0


def test_tcp_transport(server):
    u, p = turn.make_credentials(b"secreto-de-prueba", 7, 1)
    s = socket.create_connection(("127.0.0.1", server.port), timeout=2)
    tid = turn.secrets.token_bytes(12)
    s.sendall(turn.build(turn.ALLOCATE, turn.REQUEST, tid, [(turn.A_REQUESTED_TRANSPORT, b"\x11\x00\x00\x00")]))
    first = turn.parse(s.recv(65535))
    tid = turn.secrets.token_bytes(12)
    attrs = [(turn.A_REQUESTED_TRANSPORT, b"\x11\x00\x00\x00"), (turn.A_USERNAME, u.encode()),
             (turn.A_REALM, first.get(turn.A_REALM)), (turn.A_NONCE, first.get(turn.A_NONCE))]
    s.sendall(turn.build(turn.ALLOCATE, turn.REQUEST, tid, attrs, turn.long_term_key(u, p)))
    resp = turn.parse(s.recv(65535))
    assert resp.cls == turn.SUCCESS and resp.get(turn.A_XOR_RELAYED)
    s.close()


def test_reserved_range_falls_back_to_a_free_port():
    """Windows puede reservar el rango entero (Hyper-V, WSL, Docker): la llamada debe seguir funcionando."""
    taken = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    taken.bind(("127.0.0.1", 0))
    busy = taken.getsockname()[1]
    srv = turn.TurnServer(0, b"secreto-de-prueba", lambda ip: True, lambda u, c: True, port_range=(busy, busy))
    srv.start()
    try:
        c = client(srv, 7)
        resp = c.allocate()
        assert resp.cls == turn.SUCCESS and c.relayed(resp)[1] not in (0, busy)
    finally:
        srv.stop()
        taken.close()
