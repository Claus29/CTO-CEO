"""Avisos de Windows en segundo plano.

El programa de cada equipo pregunta al servidor por los avisos nuevos de la persona que ha iniciado sesión y los
muestra como avisos de Windows, aunque la ventana esté cerrada en la bandeja. Así no dependen de que la interfaz esté
abierta ni se duplican (la interfaz solo muestra sus avisos dentro de la ventana cuando está delante).

- Respeta las preferencias de cada tipo de aviso, «No molestar» y el horario de silencio (lo urgente sale siempre).
- No avisa por Windows si la ventana está delante: ahí ya se ve el aviso dentro del programa.
- Agrupa los mensajes del chat por canal y resume si llegan muchos avisos de golpe.
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from datetime import datetime, timezone

POLL_SECONDS = 4
PREFS_SECONDS = 300
BURST = 4            # más de estos avisos a la vez → se muestra un resumen
FOCUS_STALE = 20     # si la interfaz no informa en este tiempo, se considera que no está delante


def in_quiet_hours(now_hhmm: str, start: str | None, end: str | None) -> bool:
    if not start or not end or start == end:
        return False
    if start < end:
        return start <= now_hhmm < end
    return now_hhmm >= start or now_hhmm < end      # cruza la medianoche (22:00 → 07:30)


def group_key(n: dict) -> str | None:
    """Mensajes del mismo canal se agrupan: «Luis en #general» → «#general»."""
    if n.get("type") == "message_new" and " en " in (n.get("title") or ""):
        return n["title"].split(" en ", 1)[1]
    return None


def plan(items: list[dict]) -> list[dict]:
    """Convierte los avisos nuevos en los avisos de Windows que se van a mostrar (agrupados y sin ráfagas)."""
    out: list[dict] = []
    groups: dict[str, list[dict]] = {}
    for n in items:
        g = group_key(n)
        if g:
            groups.setdefault(g, []).append(n)
            if len(groups[g]) == 1:
                out.append({"group": g, "items": groups[g]})
        else:
            out.append({"group": None, "items": [n]})
    toasts = []
    for entry in out:
        its = entry["items"]
        last = its[-1]
        if len(its) == 1:
            toasts.append({"id": last["id"], "title": last["title"], "body": last.get("body") or "",
                           "urgent": bool(last.get("urgent")), "tag": f"n{last['id']}"})
        else:
            toasts.append({"id": last["id"], "title": f"{len(its)} mensajes nuevos en {entry['group']}",
                           "body": f"{last['title'].split(' en ')[0]}: {last.get('body') or ''}", "urgent": False,
                           "tag": f"g{entry['group']}"[:60]})
    urgent = [t for t in toasts if t["urgent"]]
    normal = [t for t in toasts if not t["urgent"]]
    if len(normal) > BURST:
        shown = normal[:BURST - 1]
        rest = normal[BURST - 1:]
        shown.append({"id": rest[-1]["id"], "title": f"{len(rest)} avisos nuevos más",
                      "body": " · ".join(t["title"] for t in rest[:3]) + ("…" if len(rest) > 3 else ""),
                      "urgent": False, "tag": "resumen", "summary": True})
        normal = shown
    return urgent + normal


class Notifier:
    def __init__(self, agent):
        self.agent = agent
        self.lock = threading.Lock()
        self.poll_lock = threading.Lock()
        self.token: str | None = None
        self.user_id: int | None = None
        self.server_key: str | None = None
        self.last_id: int | None = None
        self.focused_at = 0.0
        self.focused = False
        self.in_call = False
        self.route = ""
        self.prefs: dict | None = None
        self.prefs_at: float | None = None   # None = hay que pedirlas ya
        self.shown: deque = deque(maxlen=200)
        self.wake = threading.Event()

    # -- lo que cuenta la interfaz ------------------------------------------------------------------------
    def set_session(self, token: str, user_id: int | None) -> None:
        with self.lock:
            same = token == self.token and self.server_key == self.agent.server_key
            self.token, self.user_id, self.server_key = token, user_id, self.agent.server_key
            if not same:
                self.last_id = self._stored_last_id()
            self.prefs_at = None           # la interfaz lo vuelve a llamar al guardar las preferencias
        self.wake.set()

    def clear_session(self) -> None:
        with self.lock:
            self.token = self.user_id = None
            self.last_id = None

    def presence(self, focused: bool, in_call: bool = False, route: str = "") -> None:
        with self.lock:
            self.focused, self.in_call, self.route = bool(focused), bool(in_call), str(route)[:200]
            self.focused_at = time.monotonic()

    def window_in_front(self) -> bool:
        return self.focused and time.monotonic() - self.focused_at < FOCUS_STALE

    def status(self) -> dict:
        return {"active": bool(self.token), "in_call": self.in_call, "user_id": self.user_id}

    # -- último aviso visto (por servidor y usuario: tras reiniciar no se repiten) ---------------------
    def _state_key(self) -> str:
        return f"{self.server_key}|{self.user_id}"

    def _stored_last_id(self) -> int | None:
        data = self.agent._load_json("avisos.json", {})
        value = data.get(self._state_key())
        return int(value) if isinstance(value, int) else None

    def _store_last_id(self, value: int) -> None:
        data = self.agent._load_json("avisos.json", {})
        data[self._state_key()] = value
        self.agent._save_json("avisos.json", data)

    # -- servidor ---------------------------------------------------------------------------------------
    def _get(self, path: str):
        conn, resp = self.agent.send("GET", path, {"Authorization": f"Bearer {self.token}"}, timeout=10)
        try:
            body = resp.read()
            if resp.status == 401:
                self.clear_session()     # sesión cerrada o caducada: la interfaz volverá a darla al entrar
                return None
            if resp.status != 200:
                return None
            return json.loads(body or b"{}")
        finally:
            conn.close()

    def _desktop_allowed(self, n: dict) -> bool:
        prefs = self.prefs or {}
        if n.get("type") in ("call_incoming",):
            return False                 # las llamadas ya avisan por su cuenta (con tono y ventana delante)
        off = {i["type"] for i in prefs.get("items", []) if not i.get("desktop", True)}
        if n.get("type") in off:
            return False
        if n.get("urgent"):
            return True
        mute = prefs.get("mute_until")
        if mute:
            try:
                if datetime.fromisoformat(mute.replace("Z", "+00:00")) > datetime.now(timezone.utc):
                    return False
            except ValueError:
                pass
        s = prefs.get("settings") or {}
        return not in_quiet_hours(datetime.now().strftime("%H:%M"), s.get("quiet_from"), s.get("quiet_to"))

    def poll_once(self) -> list[dict]:
        """Devuelve (y muestra) los avisos de Windows de esta ronda. Una sola ronda a la vez."""
        with self.poll_lock:
            return self._poll()

    def _poll(self) -> list[dict]:
        with self.lock:
            token = self.token
        if not token or not self.agent.reachable:
            return []
        if self.prefs_at is None or self.prefs is None or time.monotonic() - self.prefs_at > PREFS_SECONDS:
            prefs = self._get("/api/notifications/prefs")
            if prefs is not None:
                self.prefs, self.prefs_at = prefs, time.monotonic()
        if self.last_id is None:
            # Primera vez en este equipo: se empieza desde ahora, sin inundar con avisos antiguos.
            data = self._get("/api/notifications?limit=1")
            if data is not None:
                self.last_id = max([i["id"] for i in data.get("items", [])] or [0])
                self._store_last_id(self.last_id)
            return []
        data = self._get(f"/api/notifications?after={self.last_id}&limit=50")
        if not data:
            return []
        items = sorted(data.get("items", []), key=lambda n: n["id"])
        if not items:
            return []
        self.last_id = items[-1]["id"]
        self._store_last_id(self.last_id)
        fresh = [n for n in items if not n.get("read_at") and n["id"] not in self.shown]
        self.shown.extend(n["id"] for n in fresh)
        if self.window_in_front():
            return []                    # se ven dentro del programa
        toasts = plan([n for n in fresh if self._desktop_allowed(n)])
        show = self.agent.hooks.get("toast")
        for t in toasts:
            if show:
                show(t["title"], t["body"], f"devcontrol://aviso/{t['id']}", t["urgent"], t["tag"])
            elif "notify" in self.agent.hooks:
                self.agent.hooks["notify"](t["title"], t["body"], t["urgent"])
        return toasts

    def loop(self) -> None:
        while not self.agent.stop_event.is_set():
            try:
                self.poll_once()
            except Exception:
                self.agent.log.debug("No se pudieron consultar los avisos", exc_info=True)
            self.wake.wait(POLL_SECONDS)
            self.wake.clear()
