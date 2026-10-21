"""Llamadas entre cuentas: timbre en todos los equipos, ocupado, cruzadas, caídas, grupos y repetidor."""
import time
import uuid

import pytest
from conftest import create_user

from devcontrol.server import calls as calls_mod


class Device:
    """Un equipo con DEV CONTROL abierto: mantiene su consulta de eventos."""

    def __init__(self, client):
        self.c = client
        self.id = uuid.uuid4().hex
        self.seq = 0
        self.events = []

    def poll(self, wait=0):
        r = self.c.get(f"/api/calls/events?client={self.id}&since={self.seq}&wait={wait}")
        self.seq = r["seq"]
        self.events += r["events"]
        return r

    def wait_for(self, etype, timeout=5, **match):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for e in self.events:
                if e["type"] == etype and all(e.get(k) == v for k, v in match.items()):
                    self.events.remove(e)
                    return e
            self.poll(wait=1)
        raise AssertionError(f"No llegó el evento {etype} {match}; eventos: {self.events}")

    def call(self, user_ids=None, kind="voz", channel_id=None, uid=None, status=(200, 201)):
        body = {"client": self.id, "kind": kind, "uid": uid or uuid.uuid4().hex}
        if channel_id:
            body["channel_id"] = channel_id
        else:
            body["user_ids"] = user_ids
        return self.c.ok("POST", "/api/calls", body, status=status)

    def act(self, call_id, action, **extra):
        return self.c.post(f"/api/calls/{call_id}/{action}", {"client": self.id, **extra})


def uid_of(client):
    return client.get("/api/me")["user"]["id"]


@pytest.fixture
def team(admin):
    b = create_user(admin, "berta", "dev")
    c = create_user(admin, "carlos", "dev")
    return admin, b, c


def test_one_to_one_full_cycle(team):
    a, b, _ = team
    da, db = Device(a), Device(b)
    da.poll(), db.poll()
    call = da.call([uid_of(b)])["call"]
    assert call["status"] == "llamando"
    incoming = db.wait_for("incoming")
    assert incoming["call"]["id"] == call["id"] and incoming["call"]["created_by_name"] == "Ana"
    acc = db.act(call["id"], "accept")
    assert acc["peers"] == [{"user_id": uid_of(a), "client": da.id}]
    st = da.wait_for("state")
    while {p["state"] for p in st["call"]["participants"]} != {"dentro"}:
        st = da.wait_for("state")
    # señal WebRTC entre los dos equipos
    db.act(call["id"], "signal", to=da.id, data={"sdp": "oferta"})
    sig = da.wait_for("signal")
    assert sig["from_client"] == db.id and sig["data"] == {"sdp": "oferta"}
    # credenciales del repetidor solo para participantes
    ice = b.get(f"/api/calls/{call['id']}/ice")
    assert ice["available"] and ice["username"].endswith(f":{uid_of(b)}:{call['id']}")
    time.sleep(1.1)
    db.act(call["id"], "leave")
    ended = da.wait_for("ended")
    assert ended["status"] == "finalizada"
    hist = a.get("/api/calls/history")["items"][0]
    assert hist["status"] == "finalizada" and hist["duration_seconds"] >= 1 and not hist["missed"]
    assert b.call("GET", f"/api/calls/{call['id']}/ice")[0] == 410


def test_rings_on_every_device_and_stops_when_answered(team):
    a, b, _ = team
    da, b1, b2 = Device(a), Device(b), Device(b)
    for d in (da, b1, b2):
        d.poll()
    call = da.call([uid_of(b)])["call"]
    b1.wait_for("incoming"), b2.wait_for("incoming")
    b2.act(call["id"], "accept")
    stop = b1.wait_for("ring_stopped")
    assert stop["reason"] == "contestada_en_otro_equipo"
    with pytest.raises(AssertionError):
        b1.act(call["id"], "accept")          # ya contestada en el otro equipo
    assert b2.act(call["id"], "accept")["call"]["me"] == "dentro"   # doble clic: misma respuesta


def test_busy_user_and_missed_notification(team):
    a, b, c = team
    da, db, dc = Device(a), Device(b), Device(c)
    for d in (da, db, dc):
        d.poll()
    call = da.call([uid_of(b)])["call"]
    db.wait_for("incoming")
    db.act(call["id"], "accept")
    r = dc.call([uid_of(a)])
    assert r.get("ended") and "ocupado" in r["reason"].lower()
    busy = da.wait_for("busy_call")
    assert busy["from_name"] == "Carlos"
    assert "call_missed" in [n["type"] for n in a.get("/api/notifications")["items"]]
    code, err = dc.c.call("POST", "/api/calls", {"client": dc.id, "user_ids": [uid_of(b)], "uid": uuid.uuid4().hex})
    assert code == 200  # Berta también está ocupada: termina sin bloquear a Carlos
    # quien está en una llamada no puede empezar otra
    code, err = da.c.call("POST", "/api/calls", {"client": da.id, "user_ids": [uid_of(c)], "uid": uuid.uuid4().hex})
    assert code == 400 and err["error"] == "en_llamada"


def test_crossed_calls_merge(team):
    a, b, _ = team
    da, db = Device(a), Device(b)
    da.poll(), db.poll()
    first = da.call([uid_of(b)])["call"]
    db.wait_for("incoming")
    second = db.call([uid_of(a)])
    assert second["merged"] and second["call"]["id"] == first["id"] and second["call"]["status"] == "en_curso"


def test_caller_cancels_before_answer(team):
    a, b, _ = team
    da, db = Device(a), Device(b)
    da.poll(), db.poll()
    call = da.call([uid_of(b)], kind="video")["call"]
    db.wait_for("incoming")
    da.act(call["id"], "leave")
    assert db.wait_for("ring_stopped")["reason"] == "cancelada"
    notes = b.get("/api/notifications")["items"]
    assert notes[0]["type"] == "call_missed" and "Ana" in notes[0]["title"]
    assert a.get("/api/calls/history")["items"][0]["status"] == "cancelada"


def test_ring_timeout(team, monkeypatch):
    monkeypatch.setattr(calls_mod, "RING_SECONDS", 0.5)
    a, b, _ = team
    da, db = Device(a), Device(b)
    da.poll(), db.poll()
    call = da.call([uid_of(b)])["call"]
    assert db.wait_for("ring_stopped")["reason"] == "sin_respuesta"
    assert da.wait_for("ended", call_id=call["id"])["status"] == "no_contestada"


def test_lost_connection_leaves_call(team, monkeypatch):
    a, b, _ = team
    da, db = Device(a), Device(b)
    da.poll(), db.poll()
    call = da.call([uid_of(b)])["call"]
    db.wait_for("incoming")
    db.act(call["id"], "accept")
    monkeypatch.setattr(calls_mod, "CLIENT_TIMEOUT", 1)
    time.sleep(1.2)                     # Berta deja de consultar (se le colgó el equipo)
    da.poll()                           # Ana sigue conectada
    left = da.wait_for("left", timeout=6)
    assert left["reason"] == "conexion_perdida"
    da.wait_for("ended")


def test_unavailable_users(team):
    a, b, c = team
    da = Device(a)
    da.poll()
    r = da.call([uid_of(c)])                       # Carlos no tiene el programa abierto
    assert r["ended"] and "No está disponible" in r["reason"]
    future = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() + 3600))
    b.ok("PATCH", "/api/me", {"mute_until": future})
    Device(b).poll()
    r = da.call([uid_of(b)])
    assert r["ended"]
    assert "call_missed" in [n["type"] for n in b.get("/api/notifications")["items"]]


def test_group_call_in_channel(team):
    a, b, c = team
    da, db, dc = Device(a), Device(b), Device(c)
    for d in (da, db, dc):
        d.poll()
    channel = a.get("/api/channels")["items"][0]["id"]
    call = da.call(channel_id=channel)["call"]
    assert len(call["participants"]) == 3
    db.wait_for("incoming"), dc.wait_for("incoming")
    db.act(call["id"], "accept")
    dc.act(call["id"], "decline")
    st = da.wait_for("state")
    assert st["call"]["status"] in ("llamando", "en_curso")
    # Carlos puede unirse después a la llamada del canal
    assert [c_["id"] for c_ in c.get("/api/calls/active")["joinable"]] == [call["id"]]
    acc = dc.act(call["id"], "join")
    assert {p["client"] for p in acc["peers"]} == {da.id, db.id}
    db.act(call["id"], "leave")
    dc.act(call["id"], "leave")         # queda una sola persona: termina
    da.wait_for("ended")
    msgs = a.get(f"/api/messages?channel={channel}")["items"]
    assert any("Llamada de voz" in m["body"] for m in msgs)


def test_permissions_and_idempotent_create(team, admin):
    a, b, _ = team
    observer = create_user(admin, "olga", "observer")
    assert observer.call("GET", f"/api/calls/events?client={uuid.uuid4().hex}")[0] == 403
    da, db = Device(a), Device(b)
    da.poll(), db.poll()
    key = uuid.uuid4().hex
    one = da.call([uid_of(b)], uid=key)["call"]
    two = da.call([uid_of(b)], uid=key)["call"]
    assert one["id"] == two["id"]
    assert b.call("GET", "/api/calls/999/ice")[0] == 410


def test_server_shutdown_ends_calls(layout, team, server):
    a, b, _ = team
    da, db = Device(a), Device(b)
    da.poll(), db.poll()
    call = da.call([uid_of(b)])["call"]
    db.wait_for("incoming")
    db.act(call["id"], "accept")
    server.calls.shutdown()
    assert da.wait_for("ended")["reason"] == "El servidor se ha detenido."
    assert server.db.scalar("SELECT status FROM calls WHERE id=?", (call["id"],)) == "finalizada"


def test_health_announces_calls(admin):
    h = admin.get("/api/health")
    assert "calls" in h["features"] and h["call_port"]
