"""Llamadas de voz y vídeo entre cuentas (de 2 personas o de grupo).

Se llama a una CUENTA, no a un equipo: la llamada suena en todos los equipos en los que la persona tiene
DEV CONTROL abierto y sigue en el primero que acepta. El servidor hace de centralita (avisos y señales WebRTC por
un canal propio de baja latencia) y de repetidor (TURN, ver turn.py) cuando los equipos no pueden verse.

Reglas de fiabilidad:
  * Cada equipo mantiene abierta una consulta de eventos; si deja de hacerlo durante CLIENT_TIMEOUT segundos se le
    da por desconectado y sale de la llamada sin bloquear a nadie.
  * Toda acción es idempotente (pulsar dos veces no duplica nada) y el estado vive en un único sitio (Hub).
  * Las llamadas cruzadas entre dos personas se unen en una sola; quien ya está en una llamada aparece «Ocupado».
  * Al reiniciar el servidor no quedan llamadas fantasma: las abiertas se cierran al arrancar.
"""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timezone

from . import auth
from .common import clean_int, log_activity, names, notify, visible_project_ids
from .db import new_uid, now_iso, parse_iso, seconds_between
from .turn import make_credentials
from .web import HttpError, bad_request, forbidden, not_found

RING_SECONDS = 45
CLIENT_TIMEOUT = 20
POLL_WAIT = 10
MAX_PARTICIPANTS = 8
MAX_SIGNAL = 64 * 1024
CLIENT_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
KINDS = {"voz": "Llamada de voz", "video": "Videollamada"}
STATUS_LABELS = {"llamando": "Llamando", "en_curso": "En curso", "finalizada": "Finalizada", "perdida": "Perdida",
                 "cancelada": "Cancelada", "rechazada": "Rechazada", "no_contestada": "No contestada"}


def fmt_minutes(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} s"
    m = round(seconds / 60)
    return f"{m} min" if m < 60 else f"{m // 60} h {m % 60} min"


class Hub:
    """Estado en memoria de los equipos conectados y de las llamadas activas."""

    def __init__(self, app):
        self.app = app
        self.db = app.db
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.clients: dict[str, dict] = {}
        self.calls: dict[int, dict] = {}
        self.stop_event = threading.Event()

    # -- equipos conectados ------------------------------------------------------------------
    def alive(self, cid: str) -> bool:
        c = self.clients.get(cid)
        # Un equipo sano vuelve a consultar como mucho cada POLL_WAIT segundos; una consulta colgada no cuenta.
        return bool(c) and time.monotonic() - c["last"] < CLIENT_TIMEOUT

    def user_clients(self, user_id: int) -> list[str]:
        return [cid for cid, c in self.clients.items() if c["user"] == user_id and self.alive(cid)]

    def push(self, cid: str, etype: str, **payload) -> None:
        c = self.clients.get(cid)
        if not c:
            return
        c["seq"] += 1
        c["events"].append({"seq": c["seq"], "type": etype, **payload})
        del c["events"][:-200]
        self.cond.notify_all()

    def push_user(self, user_id: int, etype: str, exclude: str | None = None, **payload) -> None:
        for cid in self.user_clients(user_id):
            if cid != exclude:
                self.push(cid, etype, **payload)

    def poll(self, user: dict, cid: str, since: int, wait: float) -> dict:
        with self.lock:
            c = self.clients.get(cid)
            if c and c["user"] != user["id"]:
                raise bad_request("Identificador de equipo no válido.")
            if not c:
                c = self.clients[cid] = {"user": user["id"], "last": time.monotonic(), "events": [], "seq": 0,
                                         "waiting": 0, "name": user["display_name"]}
                # Si lo están llamando ahora mismo, este equipo también debe sonar.
                for call in self.calls.values():
                    p = call["parts"].get(user["id"])
                    if p and p["state"] == "llamando":
                        self.push(cid, "incoming", call=self.snapshot(call, user["id"]))
            c["last"] = time.monotonic()
            c["events"] = [e for e in c["events"] if e["seq"] > since]
            deadline = time.monotonic() + wait
            c["waiting"] += 1
            try:
                while not c["events"] and not self.stop_event.is_set():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    self.cond.wait(min(remaining, 2.0))
            finally:
                c["waiting"] -= 1
                c["last"] = time.monotonic()
            return {"events": list(c["events"]), "seq": c["seq"],
                    "active": self.snapshot(self.call_of(user["id"]), user["id"]) if self.call_of(user["id"]) else None}

    # -- consultas -------------------------------------------------------------------------------
    def call_of(self, user_id: int) -> dict | None:
        """Llamada en la que está dentro (o que está iniciando) esa persona."""
        for call in self.calls.values():
            p = call["parts"].get(user_id)
            if p and p["state"] == "dentro":
                return call
        return None

    def snapshot(self, call: dict | None, viewer: int | None = None) -> dict | None:
        if not call:
            return None
        un = names(self.db.conn)
        parts = [{"user_id": uid, "name": un.get(uid, f"#{uid}"), "state": p["state"], "client": p["client"],
                  "joined_at": p["joined_at"], "invited_by": p["invited_by"]} for uid, p in call["parts"].items()]
        me = call["parts"].get(viewer) if viewer else None
        return {"id": call["id"], "uid": call["uid"], "kind": call["kind"], "channel_id": call["channel_id"],
                "channel_name": call.get("channel_name"), "created_by": call["created_by"],
                "created_by_name": un.get(call["created_by"]), "status": call["status"],
                "answered_at": call["answered_at"], "created_at": call["created_at"], "participants": parts,
                "me": me["state"] if me else None, "max_participants": MAX_PARTICIPANTS}

    def broadcast_state(self, call: dict) -> None:
        for uid, p in call["parts"].items():
            if p["state"] == "dentro" and p["client"]:
                self.push(p["client"], "state", call=self.snapshot(call, uid))

    # -- ciclo de vida -------------------------------------------------------------------------------
    def create(self, user: dict, cid: str, kind: str, user_ids: list[int], channel: dict | None, call_uid: str) -> dict:
        db = self.db
        with self.lock:
            for call in self.calls.values():   # idempotente: el mismo uid devuelve la misma llamada
                if call["uid"] == call_uid:
                    return {"call": self.snapshot(call, user["id"])}
            current = self.call_of(user["id"])
            if current:
                raise bad_request("Ya estás en una llamada. Cuélgala antes de empezar otra.", "en_llamada")
            if channel:   # en un canal solo puede haber una llamada: si ya existe, se entra en ella
                for call in self.calls.values():
                    if call["channel_id"] == channel["id"]:
                        return self.join(user, cid, call["id"])
            targets = [u for u in dict.fromkeys(user_ids) if u != user["id"]]
            if not targets:
                raise bad_request("No hay nadie más a quien llamar.")
            if len(targets) + 1 > MAX_PARTICIPANTS:
                raise bad_request(f"Una llamada admite como máximo {MAX_PARTICIPANTS} personas.")
            # Llamadas cruzadas: si la otra persona me está llamando a mí, se acepta su llamada.
            if len(targets) == 1 and not channel:
                for call in self.calls.values():
                    mine = call["parts"].get(user["id"])
                    if call["created_by"] == targets[0] and mine and mine["state"] == "llamando" and \
                            len(call["parts"]) == 2:
                        return {**self.accept(user, cid, call["id"]), "merged": True}
            ts = now_iso()
            with db.tx() as c:
                call_id = c.execute("INSERT INTO calls(uid, kind, channel_id, created_by, created_at, status) "
                                    "VALUES (?,?,?,?,?, 'llamando')",
                                    (call_uid, kind, channel["id"] if channel else None, user["id"], ts)).lastrowid
                c.execute("INSERT INTO call_participants(call_id, user_id, state, joined_at) VALUES (?,?, 'dentro', ?)",
                          (call_id, user["id"], ts))
            call = {"id": call_id, "uid": call_uid, "kind": kind, "channel_id": channel["id"] if channel else None,
                    "channel_name": channel["name"] if channel else None, "created_by": user["id"], "created_at": ts,
                    "answered_at": None, "status": "llamando",
                    "parts": {user["id"]: {"state": "dentro", "client": cid, "since": time.monotonic(),
                                           "joined_at": ts, "invited_by": None}}}
            self.calls[call_id] = call
            self._invite(call, user, targets)
            self._maybe_end(call)
            if call["id"] not in self.calls:   # nadie podía contestar
                return {"call": self.snapshot(call, user["id"]), "ended": True, "reason": call.get("end_reason")}
            return {"call": self.snapshot(call, user["id"])}

    def _invite(self, call: dict, inviter: dict, targets: list[int]) -> None:
        db = self.db
        un = names(db.conn)
        with db.tx() as c:
            for uid in targets:
                prev = call["parts"].get(uid)
                if prev and prev["state"] in ("dentro", "llamando"):
                    continue
                row = c.execute("SELECT active, mute_until FROM users WHERE id=?", (uid,)).fetchone()
                if not row or not row[0]:
                    continue
                perms = self.app.perms.for_role(c.execute("SELECT role_id FROM users WHERE id=?", (uid,)).fetchone()[0])
                if "llamadas.usar" not in perms:
                    state, why = "no_disponible", "no tiene permiso para llamadas"
                elif row[1] and row[1] > now_iso():
                    state, why = "no_disponible", "no disponible (no molestar)"
                elif self.call_of(uid):
                    state, why = "ocupado", "está en otra llamada"
                elif not self.user_clients(uid):
                    state, why = "no_disponible", "no está conectado"
                else:
                    state, why = "llamando", ""
                call["parts"][uid] = {"state": state, "client": None, "since": time.monotonic(), "joined_at": None,
                                      "invited_by": inviter["id"]}
                c.execute("INSERT INTO call_participants(call_id, user_id, state, invited_by) VALUES (?,?,?,?) "
                          "ON CONFLICT(call_id, user_id) DO UPDATE SET state=excluded.state, invited_by=excluded.invited_by",
                          (call["id"], uid, state, inviter["id"]))
                if state == "llamando":
                    self.push_user(uid, "incoming", call=self.snapshot(call, uid))
                else:
                    self._notify_missed(c, call, uid, busy=state == "ocupado")
                    self.push_user(call["created_by"], "notice", call_id=call["id"],
                                   text=f"{un.get(uid, 'Esa persona')} {why}.")
                    if state == "ocupado":
                        self.push_user(uid, "busy_call", call_id=call["id"], from_user=inviter["id"],
                                       from_name=inviter["display_name"], kind=call["kind"])
        self.broadcast_state(call)

    def invite(self, user: dict, call_id: int, user_ids: list[int]) -> dict:
        with self.lock:
            call = self._get(call_id)
            if call["parts"].get(user["id"], {}).get("state") != "dentro":
                raise forbidden("Solo quien está en la llamada puede añadir personas.")
            new = [u for u in dict.fromkeys(user_ids) if call["parts"].get(u, {}).get("state") not in ("dentro", "llamando")]
            active = sum(1 for p in call["parts"].values() if p["state"] in ("dentro", "llamando"))
            if active + len(new) > MAX_PARTICIPANTS:
                raise bad_request(f"Una llamada admite como máximo {MAX_PARTICIPANTS} personas.")
            self._invite(call, user, new)
            return {"call": self.snapshot(call, user["id"])}

    def accept(self, user: dict, cid: str, call_id: int) -> dict:
        with self.lock:
            call = self._get(call_id)
            p = call["parts"].get(user["id"])
            if p and p["state"] == "dentro":
                if p["client"] == cid:   # doble clic: misma respuesta
                    return {"call": self.snapshot(call, user["id"]), "peers": self._peers(call, user["id"])}
                if self.alive(p["client"]):
                    raise bad_request("Ya estás en esta llamada desde otro equipo.", "en_otro_equipo")
            elif not p or p["state"] not in ("llamando",):
                raise bad_request("Esta llamada ya no te está llamando.", "llamada_no_disponible")
            current = self.call_of(user["id"])
            if current and current["id"] != call_id:
                raise bad_request("Ya estás en otra llamada. Cuélgala antes de contestar.", "en_llamada")
            return self._enter(call, user, cid)

    def join(self, user: dict, cid: str, call_id: int) -> dict:
        """Entrar en una llamada en curso (de un canal, o volver tras recargar o perder la conexión)."""
        with self.lock:
            call = self._get(call_id)
            p = call["parts"].get(user["id"])
            if p and p["state"] == "dentro" and p["client"] == cid:
                return {"call": self.snapshot(call, user["id"]), "peers": self._peers(call, user["id"])}
            if p and p["state"] == "dentro" and self.alive(p["client"]) and p["client"] != cid:
                raise bad_request("Ya estás en esta llamada desde otro equipo.", "en_otro_equipo")
            if not p and not (call["channel_id"] and self._can_see_channel(user, call["channel_id"])):
                raise forbidden("No participas en esta llamada.")
            current = self.call_of(user["id"])
            if current and current["id"] != call_id:
                raise bad_request("Ya estás en otra llamada. Cuélgala antes de entrar en esta.", "en_llamada")
            active = sum(1 for q in call["parts"].values() if q["state"] in ("dentro", "llamando"))
            if not (p and p["state"] in ("dentro", "llamando")) and active >= MAX_PARTICIPANTS:
                raise bad_request(f"La llamada está completa ({MAX_PARTICIPANTS} personas).")
            return self._enter(call, user, cid)

    def _enter(self, call: dict, user: dict, cid: str) -> dict:
        ts = now_iso()
        old = call["parts"].get(user["id"])
        if old and old["state"] == "dentro" and old["client"] and old["client"] != cid:
            self._notify_left(call, user["id"], old["client"], "reconexion")
        call["parts"][user["id"]] = {"state": "dentro", "client": cid, "since": time.monotonic(), "joined_at": ts,
                                     "invited_by": old["invited_by"] if old else None}
        with self.db.tx() as c:
            c.execute("INSERT INTO call_participants(call_id, user_id, state, joined_at) VALUES (?,?, 'dentro', ?) "
                      "ON CONFLICT(call_id, user_id) DO UPDATE SET state='dentro', joined_at=COALESCE(joined_at, ?), "
                      "left_at=NULL", (call["id"], user["id"], ts, ts))
            if user["id"] != call["created_by"] and not call["answered_at"]:
                call["answered_at"] = ts
                call["status"] = "en_curso"
                c.execute("UPDATE calls SET status='en_curso', answered_at=? WHERE id=?", (ts, call["id"]))
        self.push_user(user["id"], "ring_stopped", exclude=cid, call_id=call["id"], reason="contestada_en_otro_equipo")
        self.broadcast_state(call)
        return {"call": self.snapshot(call, user["id"]), "peers": self._peers(call, user["id"])}

    def _peers(self, call: dict, user_id: int) -> list[dict]:
        return [{"user_id": uid, "client": p["client"]} for uid, p in call["parts"].items()
                if uid != user_id and p["state"] == "dentro" and p["client"]]

    def decline(self, user: dict, call_id: int) -> dict:
        with self.lock:
            call = self.calls.get(call_id)
            if not call:
                return {"ok": True}
            p = call["parts"].get(user["id"])
            if not p or p["state"] != "llamando":
                return {"ok": True}
            p["state"] = "rechazada"
            with self.db.tx() as c:
                c.execute("UPDATE call_participants SET state='rechazada' WHERE call_id=? AND user_id=?",
                          (call_id, user["id"]))
            self.push_user(user["id"], "ring_stopped", call_id=call_id, reason="rechazada")
            self.push_user(call["created_by"], "notice", call_id=call_id,
                           text=f"{user['display_name']} ha rechazado la llamada.")
            self.broadcast_state(call)
            self._maybe_end(call)
            return {"ok": True}

    def leave(self, user: dict, cid: str | None, call_id: int, reason: str = "colgo") -> dict:
        with self.lock:
            call = self.calls.get(call_id)
            if not call:
                return {"ok": True}
            p = call["parts"].get(user["id"])
            if not p or p["state"] != "dentro" or (cid and p["client"] and p["client"] != cid):
                return {"ok": True}
            self._set_left(call, user["id"], reason)
            self._maybe_end(call)
            return {"ok": True}

    def _set_left(self, call: dict, user_id: int, reason: str) -> None:
        p = call["parts"][user_id]
        client = p["client"]
        p["state"], p["client"] = "salio", None
        with self.db.tx() as c:
            c.execute("UPDATE call_participants SET state='salio', left_at=? WHERE call_id=? AND user_id=?",
                      (now_iso(), call["id"], user_id))
        self._notify_left(call, user_id, client, reason)
        self.broadcast_state(call)

    def _notify_left(self, call: dict, user_id: int, client: str | None, reason: str) -> None:
        for uid, q in call["parts"].items():
            if q["state"] == "dentro" and q["client"] and uid != user_id:
                self.push(q["client"], "left", call_id=call["id"], user_id=user_id, client=client, reason=reason)

    def signal(self, user: dict, cid: str, call_id: int, to_client: str, data) -> dict:
        with self.lock:
            call = self._get(call_id)
            p = call["parts"].get(user["id"])
            if not p or p["state"] != "dentro" or p["client"] != cid:
                raise HttpError(409, "fuera_de_llamada", "No estás en esta llamada.")
            if not any(q["state"] == "dentro" and q["client"] == to_client for q in call["parts"].values()):
                return {"ok": False}
            self.push(to_client, "signal", call_id=call_id, from_user=user["id"], from_client=cid, data=data)
            return {"ok": True}

    def _get(self, call_id: int) -> dict:
        call = self.calls.get(int(call_id))
        if not call:
            raise HttpError(410, "llamada_terminada", "Esta llamada ya ha terminado.")
        return call

    def _can_see_channel(self, user: dict, channel_id: int) -> bool:
        ch = self.db.query_one("SELECT project_id, archived FROM channels WHERE id=?", (channel_id,))
        if not ch or ch["archived"]:
            return False
        ids = visible_project_ids(self.db, user)
        return ch["project_id"] is None or ids is None or ch["project_id"] in ids

    # -- fin de llamada ------------------------------------------------------------------------
    def _maybe_end(self, call: dict) -> None:
        if call["id"] not in self.calls:
            return
        parts = call["parts"]
        inside = [u for u, p in parts.items() if p["state"] == "dentro"]
        ringing = [u for u, p in parts.items() if p["state"] == "llamando"]
        if not call["answered_at"]:
            if call["created_by"] not in inside:
                self._end(call, "cancelada", "Ha colgado antes de que contestaran.")
            elif not ringing:
                states = {p["state"] for u, p in parts.items() if u != call["created_by"]}
                if states == {"rechazada"}:
                    self._end(call, "rechazada", "Han rechazado la llamada.")
                elif "ocupado" in states and len(parts) == 2:
                    self._end(call, "no_contestada", "Está ocupado en otra llamada.")
                elif "no_disponible" in states and len(parts) == 2:
                    self._end(call, "no_contestada", "No está disponible.")
                else:
                    self._end(call, "no_contestada", "Nadie ha contestado.")
        elif len(inside) <= 1 and not ringing:
            self._end(call, "finalizada", "La llamada ha terminado.")

    def _end(self, call: dict, status: str, reason: str) -> None:
        self.calls.pop(call["id"], None)
        call["status"], call["end_reason"] = status, reason
        ts = now_iso()
        db = self.db
        un = names(db.conn)
        with db.tx() as c:
            for uid, p in call["parts"].items():
                if p["state"] == "llamando":
                    p["state"] = "perdida"
                    self._notify_missed(c, call, uid)
                    self.push_user(uid, "ring_stopped", call_id=call["id"], reason=status)
                    c.execute("UPDATE call_participants SET state='perdida' WHERE call_id=? AND user_id=?",
                              (call["id"], uid))
                elif p["state"] == "dentro":
                    if p["client"]:
                        self.push(p["client"], "ended", call_id=call["id"], status=status, reason=reason)
                    p["state"] = "salio"
                    c.execute("UPDATE call_participants SET state='salio', left_at=? WHERE call_id=? AND user_id=?",
                              (ts, call["id"], uid))
            c.execute("UPDATE calls SET status=?, ended_at=?, end_reason=? WHERE id=?", (status, ts, reason, call["id"]))
            joined = [un.get(u, "?") for u, p in call["parts"].items() if p.get("joined_at")]
            duration = seconds_between(call["answered_at"], ts) if call["answered_at"] else 0
            kind = KINDS.get(call["kind"], "Llamada")
            if call["answered_at"]:
                text = f"📞 {kind} · {fmt_minutes(duration)} · {', '.join(joined)}"
            else:
                text = f"📞 {kind} de {un.get(call['created_by'], '?')} sin respuesta"
            if call["channel_id"]:
                mid = c.execute("INSERT INTO messages(uid, channel_id, user_id, body, created_at) VALUES (?,?,?,?,?)",
                                (new_uid(), call["channel_id"], call["created_by"], text, ts)).lastrowid
                db.emit(c, "message", "message", mid)
            creator = {"id": call["created_by"], "display_name": un.get(call["created_by"], "")}
            log_activity(db, c, creator, "call", call["id"], None, "call",
                         text.replace("📞 ", "") if call["answered_at"] else
                         f"Llamó a {', '.join(un.get(u, '?') for u in call['parts'] if u != call['created_by'])} "
                         f"({STATUS_LABELS.get(status, status).lower()})")
            db.emit(c, "call", "call", call["id"])
        try:
            self.app.turn_drop(call["id"])
        except Exception:
            pass

    def _notify_missed(self, c, call: dict, uid: int, busy: bool = False) -> None:
        un = names(self.db.conn)
        who = un.get(call["created_by"], "Alguien")
        if uid == call["created_by"]:
            return
        notify(self.db, c, [uid], "call_missed", f"Llamada perdida de {who}",
               ("Te llamó mientras estabas en otra llamada. " if busy else "") +
               (f"{KINDS.get(call['kind'])} en #{call['channel_name']}" if call.get("channel_name")
                else KINDS.get(call["kind"], "Llamada")),
               f"call-missed-{call['id']}-{uid}", "call", call["id"], None)

    # -- tareas periódicas ------------------------------------------------------------------------
    def sweep(self) -> None:
        now = time.monotonic()
        with self.lock:
            for call in list(self.calls.values()):
                changed = False
                for uid, p in list(call["parts"].items()):
                    if p["state"] == "llamando" and now - p["since"] >= RING_SECONDS:
                        p["state"] = "perdida"
                        with self.db.tx() as c:
                            c.execute("UPDATE call_participants SET state='perdida' WHERE call_id=? AND user_id=?",
                                      (call["id"], uid))
                            self._notify_missed(c, call, uid)
                        self.push_user(uid, "ring_stopped", call_id=call["id"], reason="sin_respuesta")
                        changed = True
                    elif p["state"] == "dentro" and p["client"] and not self.alive(p["client"]):
                        self._set_left(call, uid, "conexion_perdida")
                        changed = True
                if changed:
                    self.broadcast_state(call)
                    self._maybe_end(call)
            for cid in [cid for cid, c in self.clients.items() if c["waiting"] == 0 and now - c["last"] > 300]:
                del self.clients[cid]

    def shutdown(self) -> None:
        with self.lock:
            for call in list(self.calls.values()):
                try:
                    self._end(call, "finalizada", "El servidor se ha detenido.")
                except Exception:
                    pass
            self.stop_event.set()
            self.cond.notify_all()


def register(router, app):
    db = app.db
    hub = Hub(app)
    app.calls = hub

    # Llamadas que quedaron abiertas por un cierre inesperado: se cierran (no hay llamadas fantasma).
    with db.tx() as c:
        c.execute("UPDATE calls SET status='finalizada', ended_at=?, end_reason='Reinicio del servidor' "
                  "WHERE status IN ('llamando', 'en_curso')", (now_iso(),))
        c.execute("UPDATE call_participants SET state='salio' WHERE state IN ('dentro', 'llamando')")

    def client_id(req, data=None) -> str:
        cid = (data or {}).get("client") or req.q("client") or ""
        if not CLIENT_RE.match(str(cid)):
            raise bad_request("Falta el identificador de este equipo.")
        return str(cid)

    @router.route("GET", "/api/calls/events", perm="llamadas.usar")
    def events(req):
        wait = min(max(req.q_int("wait", POLL_WAIT), 0), POLL_WAIT)
        return hub.poll(req.user, client_id(req), req.q_int("since", 0), wait)

    @router.route("POST", "/api/calls", perm="llamadas.usar")
    def create(req):
        data = req.json
        kind = data.get("kind") or "voz"
        if kind not in KINDS:
            raise bad_request("Tipo de llamada no válido.")
        channel = None
        user_ids = [clean_int(u, "persona") for u in data.get("user_ids") or []]
        if data.get("channel_id"):
            ch = db.query_one("SELECT * FROM channels WHERE id=? AND archived=0", (clean_int(data["channel_id"], "canal"),))
            if not ch or not hub._can_see_channel(req.user, ch["id"]):
                raise not_found("El canal no existe.")
            channel = ch
            members = db.query("SELECT u.id FROM users u WHERE u.active=1") if ch["project_id"] is None else \
                db.query("SELECT u.id FROM users u WHERE u.active=1 AND (u.id IN (SELECT user_id FROM project_members "
                         "WHERE project_id=?) OR u.role_id IN (SELECT role_id FROM role_permissions WHERE "
                         "perm='proyectos.ver_todos'))", (ch["project_id"],))
            # En un canal se avisa a quien está conectado ahora (los demás pueden unirse después).
            user_ids = [m["id"] for m in members if hub.user_clients(m["id"])]
            user_ids = user_ids[:MAX_PARTICIPANTS - 1] if len(user_ids) >= MAX_PARTICIPANTS else user_ids
            if not [u for u in user_ids if u != req.user["id"]]:
                raise bad_request("No hay nadie más del canal conectado ahora mismo.", "nadie_conectado")
        call_uid = str(data.get("uid") or new_uid())
        return hub.create(req.user, client_id(req, data), kind, user_ids, channel, call_uid)

    @router.route("POST", "/api/calls/{call_id}/accept", perm="llamadas.usar")
    def accept(req, call_id):
        return hub.accept(req.user, client_id(req, req.json), int(call_id))

    @router.route("POST", "/api/calls/{call_id}/join", perm="llamadas.usar")
    def join(req, call_id):
        return hub.join(req.user, client_id(req, req.json), int(call_id))

    @router.route("POST", "/api/calls/{call_id}/decline", perm="llamadas.usar")
    def decline(req, call_id):
        return hub.decline(req.user, int(call_id))

    @router.route("POST", "/api/calls/{call_id}/leave", perm="llamadas.usar")
    def leave(req, call_id):
        data = req.json
        return hub.leave(req.user, data.get("client"), int(call_id))

    @router.route("POST", "/api/calls/{call_id}/invite", perm="llamadas.usar")
    def invite(req, call_id):
        return hub.invite(req.user, int(call_id), [clean_int(u, "persona") for u in req.json.get("user_ids") or []])

    @router.route("POST", "/api/calls/{call_id}/signal", perm="llamadas.usar")
    def signal(req, call_id):
        data = req.json
        if len(req.body) > MAX_SIGNAL:
            raise bad_request("Señal demasiado grande.")
        return hub.signal(req.user, client_id(req, data), int(call_id), str(data.get("to") or ""), data.get("data"))

    @router.route("GET", "/api/calls/{call_id}/ice", perm="llamadas.usar")
    def ice(req, call_id):
        with hub.lock:
            call = hub._get(int(call_id))
            p = call["parts"].get(req.user["id"])
            if not p or p["state"] not in ("dentro", "llamando"):
                raise forbidden("No participas en esta llamada.")
        out = {"port": app.turn_port, "available": app.turn_port is not None}
        if app.turn_port:
            out["username"], out["credential"] = make_credentials(app.turn_secret, req.user["id"], int(call_id))
        return out

    @router.route("GET", "/api/calls/active", perm="llamadas.usar")
    def active(req):
        with hub.lock:
            mine = hub.call_of(req.user["id"])
            joinable = [hub.snapshot(c, req.user["id"]) for c in hub.calls.values()
                        if c["answered_at"] and c is not mine and
                        (req.user["id"] in c["parts"] or (c["channel_id"] and hub._can_see_channel(req.user, c["channel_id"])))]
            online = sorted({c["user"] for cid, c in hub.clients.items() if hub.alive(cid)})
            return {"current": hub.snapshot(mine, req.user["id"]), "joinable": joinable, "online": online}

    @router.route("GET", "/api/calls/history", perm="llamadas.usar")
    def history(req):
        limit = min(req.q_int("limit", 50), 200)
        rows = db.query("SELECT c.*, p.state AS my_state FROM calls c JOIN call_participants p ON p.call_id=c.id "
                        "AND p.user_id=? ORDER BY c.id DESC LIMIT ?", (req.user["id"], limit))
        if not rows:
            return {"items": []}
        un = names(db.conn)
        ids = [r["id"] for r in rows]
        parts: dict[int, list] = {}
        for p in db.query(f"SELECT * FROM call_participants WHERE call_id IN ({','.join('?' * len(ids))})", ids):
            parts.setdefault(p["call_id"], []).append({"user_id": p["user_id"], "name": un.get(p["user_id"]),
                                                       "state": p["state"], "joined": bool(p["joined_at"])})
        out = []
        for r in rows:
            duration = seconds_between(r["answered_at"], r["ended_at"]) if r["answered_at"] and r["ended_at"] else 0
            missed = r["my_state"] in ("perdida", "ocupado", "no_disponible") and r["created_by"] != req.user["id"]
            out.append({**r, "created_by_name": un.get(r["created_by"]), "participants": parts.get(r["id"], []),
                        "duration_seconds": duration, "missed": missed, "outgoing": r["created_by"] == req.user["id"],
                        "status_label": STATUS_LABELS.get(r["status"], r["status"]), "kind_label": KINDS.get(r["kind"])})
        return {"items": out}

    def sweeper():
        while not hub.stop_event.wait(1):
            try:
                hub.sweep()
            except Exception:
                app.log.exception("Error en el control de llamadas")

    threading.Thread(target=sweeper, name="llamadas", daemon=True).start()
    _ = (auth, datetime, timezone, parse_iso)
