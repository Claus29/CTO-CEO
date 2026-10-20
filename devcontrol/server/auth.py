"""Contraseñas, sesiones, verificación en dos pasos (TOTP) y permisos por rol."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import struct
import threading
import time
from datetime import datetime, timedelta, timezone

from .db import Database, now_iso
from .web import HttpError, forbidden

PBKDF2_ITERATIONS = 600_000
SESSION_HOURS = 12
REMEMBER_DAYS = 30
ONLINE_SECONDS = 45

COMMON_PASSWORDS = {
    "123456", "123456789", "12345678", "password", "qwerty", "123123", "111111", "1234567890", "1234567",
    "qwerty123", "000000", "1q2w3e", "abc123", "password1", "iloveyou", "contraseña", "contrasena", "12345678910",
    "admin123", "administrador", "qwertyuiop", "123321", "654321", "666666", "987654321", "superman", "dragon",
    "football", "futbol", "monkey", "letmein", "welcome", "bienvenido", "sunshine", "princess", "master",
    "passw0rd", "p@ssw0rd", "asdfghjkl", "zxcvbnm", "1qaz2wsx", "q1w2e3r4", "barcelona", "realmadrid",
    "teamo", "tequiero", "estrella", "mariposa", "password123", "contraseña1", "devcontrol", "dev control",
}


# -- contraseñas ----------------------------------------------------------------
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_b64, hash_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), base64.b64decode(salt_b64), int(iterations))
        return hmac.compare_digest(dk, base64.b64decode(hash_b64))
    except (ValueError, TypeError):
        return False


def validate_new_password(password, min_length: int = 10, username: str = "") -> None:
    if not isinstance(password, str) or len(password) < min_length:
        raise HttpError(400, "contrasena_debil", f"La contraseña debe tener al menos {min_length} caracteres.")
    if len(password) > 200:
        raise HttpError(400, "contrasena_larga", "La contraseña es demasiado larga.")
    low = password.lower()
    if low in COMMON_PASSWORDS or (username and username.lower() in low):
        raise HttpError(400, "contrasena_comun", "Esa contraseña es demasiado común o contiene el nombre de "
                                                 "usuario. Elige otra.")
    if len(set(password)) < 4:
        raise HttpError(400, "contrasena_debil", "La contraseña repite demasiado los mismos caracteres.")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# -- TOTP (RFC 6238) ---------------------------------------------------------------
def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_code(secret: str, for_time: float | None = None, step: int = 30) -> str:
    padded = secret + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded.upper())
    counter = int((for_time if for_time is not None else time.time()) // step)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def verify_totp(secret: str, code: str) -> bool:
    code = (code or "").replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return False
    now = time.time()
    return any(hmac.compare_digest(totp_code(secret, now + d * 30), code) for d in (-1, 0, 1))


def new_recovery_codes() -> tuple[list[str], str]:
    codes = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(8)]
    return codes, json.dumps([token_hash(c) for c in codes])


def use_recovery_code(stored: str | None, code: str) -> str | None:
    """Devuelve la lista actualizada si el código es válido (y lo consume)."""
    hashes = json.loads(stored or "[]")
    h = token_hash((code or "").strip().lower())
    if h in hashes:
        hashes.remove(h)
        return json.dumps(hashes)
    return None


def otpauth_uri(secret: str, username: str) -> str:
    from urllib.parse import quote
    return f"otpauth://totp/DEV%20CONTROL:{quote(username)}?secret={secret}&issuer=DEV%20CONTROL"


# -- permisos ------------------------------------------------------------------------
class PermissionCache:
    """Permisos por rol, en memoria. Se invalida al cambiar roles."""

    def __init__(self, db: Database):
        self.db = db
        self._lock = threading.Lock()
        self._cache: dict[int, frozenset[str]] = {}

    def for_role(self, role_id: int) -> frozenset[str]:
        with self._lock:
            if role_id in self._cache:
                return self._cache[role_id]
        perms = frozenset(r["perm"] for r in self.db.query("SELECT perm FROM role_permissions WHERE role_id=?",
                                                            (role_id,)))
        with self._lock:
            self._cache[role_id] = perms
        return perms

    def invalidate(self) -> None:
        with self._lock:
            self._cache.clear()


def has_perm(user: dict | None, perm: str) -> bool:
    return bool(user) and perm in user.get("permissions", ())


def require(user: dict | None, perm: str) -> None:
    if not has_perm(user, perm):
        raise forbidden()


def require_any(user: dict | None, *perms: str) -> None:
    if not any(has_perm(user, p) for p in perms):
        raise forbidden()


def public_user(row: dict, presence: Presence | None = None, roles: dict | None = None) -> dict:
    out = {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "email": row.get("email", ""),
        "role_id": row["role_id"],
        "role_key": (roles or {}).get(row["role_id"], {}).get("key") if roles else row.get("role_key"),
        "role_label": (roles or {}).get(row["role_id"], {}).get("name") if roles else row.get("role_name"),
        "active": bool(row.get("active", 1)),
        "capacity_hours": row.get("capacity_hours", 40),
        "totp_enabled": bool(row.get("totp_enabled")),
        "must_change_password": bool(row.get("must_change_password")),
        "last_action_at": row.get("last_action_at"),
        "last_action": row.get("last_action"),
        "last_login_at": row.get("last_login_at"),
    }
    if presence is not None:
        out["online"] = presence.is_online(row["id"])
        out["last_seen_at"] = presence.last_seen_iso(row["id"]) or row.get("last_seen_at")
    return out


class Presence:
    def __init__(self):
        self._seen: dict[int, float] = {}
        self._lock = threading.Lock()

    def touch(self, user_id: int) -> None:
        with self._lock:
            self._seen[user_id] = time.time()

    def drop(self, user_id: int) -> None:
        with self._lock:
            self._seen.pop(user_id, None)

    def is_online(self, user_id: int) -> bool:
        with self._lock:
            ts = self._seen.get(user_id)
        return ts is not None and time.time() - ts < ONLINE_SECONDS

    def last_seen_iso(self, user_id: int) -> str | None:
        with self._lock:
            ts = self._seen.get(user_id)
        if ts is None:
            return None
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def online_ids(self) -> set[int]:
        now = time.time()
        with self._lock:
            return {uid for uid, ts in self._seen.items() if now - ts < ONLINE_SECONDS}


class LoginThrottle:
    def __init__(self, max_failures: int = 8, window: int = 300):
        self.max_failures = max_failures
        self.window = window
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.time()
        with self._lock:
            recent = [t for t in self._failures.get(key, []) if now - t < self.window]
            self._failures[key] = recent
            if len(recent) >= self.max_failures:
                wait = int(self.window - (now - recent[0])) + 1
                raise HttpError(429, "demasiados_intentos",
                                f"Demasiados intentos fallidos. Espera {wait} segundos y vuelve a probar.")

    def fail(self, key: str) -> int:
        with self._lock:
            self._failures.setdefault(key, []).append(time.time())
            return len(self._failures[key])

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


def create_session(db: Database, user_id: int, remember: bool, client_ip: str, label: str = "") -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires = now + (timedelta(days=REMEMBER_DAYS) if remember else timedelta(hours=SESSION_HOURS))
    expires_iso = expires.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    with db.tx() as c:
        c.execute("INSERT INTO sessions(token_hash, user_id, created_at, expires_at, remember, client_ip, "
                  "client_label, last_used_at) VALUES (?,?,?,?,?,?,?,?)",
                  (token_hash(token), user_id, now_iso(), expires_iso, 1 if remember else 0, client_ip,
                   label[:80], now_iso()))
        c.execute("UPDATE users SET last_login_at=? WHERE id=?", (now_iso(), user_id))
        c.execute("DELETE FROM sessions WHERE expires_at < ?", (now_iso(),))
    return token, expires_iso


def resolve_session(db: Database, token: str, perms: PermissionCache) -> tuple[dict, str] | None:
    th = token_hash(token)
    row = db.query_one(
        "SELECT s.expires_at, u.*, r.key AS role_key, r.name AS role_name FROM sessions s "
        "JOIN users u ON u.id = s.user_id JOIN roles r ON r.id = u.role_id "
        "WHERE s.token_hash = ? AND u.active = 1", (th,))
    if not row or row["expires_at"] < now_iso():
        return None
    row["permissions"] = perms.for_role(row["role_id"])
    return row, th
