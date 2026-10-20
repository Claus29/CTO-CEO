"""Integración con GitHub: enlaza commits y peticiones de cambios (PR) con incidencias y tareas.

Funciona consultando la API de GitHub cada pocos minutos desde el anfitrión, por lo
que el servidor nunca se expone a Internet. Un PR abierto pone el elemento «En
progreso»; un PR fusionado lo pasa a «En verificación» (nunca a resuelto directamente).
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from .common import clean_int, clean_text, log_activity, notify, users_with_perm
from .db import now_iso
from .web import HttpError, bad_request

REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def register(router, app):
    db = app.db
    state = {"last_error": "", "last_result": None}

    def cfg() -> dict:
        return db.get_setting("github") or {}

    @router.route("GET", "/api/integrations/github", perm="sistema.integraciones")
    def get_cfg(req):
        c = cfg()
        return {"repo": c.get("repo", ""), "enabled": bool(c.get("enabled")), "poll_minutes": c.get("poll_minutes", 10),
                "token_item_id": c.get("token_item_id"), "last_poll": c.get("last_poll"),
                "last_error": state["last_error"], "last_result": state["last_result"],
                "vault_unlocked": app.vault_key is not None}

    @router.route("PUT", "/api/integrations/github", perm="sistema.integraciones")
    def put_cfg(req):
        data = req.json
        c = cfg()
        repo = clean_text(data.get("repo", c.get("repo", "")), "repositorio", 120)
        if repo and not REPO_RE.match(repo):
            raise bad_request("El repositorio debe tener la forma propietario/nombre.")
        c.update({"repo": repo, "enabled": bool(data.get("enabled", c.get("enabled"))) and bool(repo),
                  "poll_minutes": clean_int(data.get("poll_minutes", c.get("poll_minutes", 10)), "minutos", 2, 240),
                  "token_item_id": clean_int(data.get("token_item_id"), "secreto", optional=True)})
        with db.tx() as conn:
            db.put_setting(conn, "github", c)
            log_activity(db, conn, req.user, "settings", None, "github", "changed",
                         f"Configuró la integración con GitHub ({repo or 'desactivada'})")
        app.settings_invalidate()
        return get_cfg(req)

    @router.route("POST", "/api/integrations/github/poll", perm="sistema.integraciones")
    def poll_now(req):
        return poll(force=True)

    def token() -> str | None:
        item_id = cfg().get("token_item_id")
        if not item_id or app.vault_key is None:
            return None
        row = db.query_one("SELECT secret_enc FROM vault_items WHERE id=?", (item_id,))
        if not row:
            return None
        try:
            import base64

            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            raw = base64.b64decode(row["secret_enc"])
            return AESGCM(app.vault_key).decrypt(raw[:12], raw[12:], None).decode()
        except Exception:
            return None

    def api(path: str):
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "DEV-CONTROL"}
        tok = token()
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
        req = urllib.request.Request(f"https://api.github.com/repos/{cfg()['repo']}{path}", headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())

    def key_pattern():
        keys = [r["key"] for r in db.query("SELECT key FROM projects")] + ["T", "DEC"]
        return re.compile(r"\b(" + "|".join(re.escape(k) for k in keys) + r")-(\d+)\b", re.IGNORECASE)

    def find_targets(text: str, pattern) -> list[tuple[str, dict]]:
        out = []
        for prefix, num in set(pattern.findall(text or "")):
            prefix = prefix.upper()
            if prefix == "T":
                row = db.query_one("SELECT * FROM tasks WHERE number=?", (int(num),))
                if row:
                    out.append(("task", row))
            elif prefix != "DEC":
                row = db.query_one("SELECT i.* FROM incidents i JOIN projects p ON p.id=i.project_id WHERE p.key=? AND "
                                   "i.number=?", (prefix, int(num)))
                if row:
                    out.append(("incident", row))
        return out

    def upsert_link(c, etype, row, kind, ref, url, title, st, author) -> str | None:
        prev = c.execute("SELECT state FROM code_links WHERE entity_type=? AND entity_id=? AND kind=? AND ref=?",
                         (etype, row["id"], kind, ref)).fetchone()
        ts = now_iso()
        if prev is None:
            c.execute("INSERT INTO code_links(entity_type, entity_id, kind, ref, url, title, state, author, created_at, "
                      "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)", (etype, row["id"], kind, ref, url, title[:300], st,
                                                                   author, ts, ts))
            return "new"
        if prev[0] != st:
            c.execute("UPDATE code_links SET state=?, title=?, updated_at=? WHERE entity_type=? AND entity_id=? AND kind=? "
                      "AND ref=?", (st, title[:300], ts, etype, row["id"], kind, ref))
            return "changed"
        return None

    def to_progress(c, etype, row, note):
        table = "incidents" if etype == "incident" else "tasks"
        if row["status"] != "pendiente":
            return
        actor = row["assignee_id"] or row["created_by"]
        c.execute(f"UPDATE {table} SET status='en_progreso', updated_at=?, updated_by=?, row_version=row_version+1 "
                  f"WHERE id=?", (now_iso(), actor, row["id"]))
        log_activity(db, c, None, etype, row["id"], row["key"], "github", f"GitHub: {note} → {row['key']} en progreso",
                     project_id=row["project_id"])

    def to_verification(c, etype, row, note, url):
        table = "incidents" if etype == "incident" else "tasks"
        if row["status"] not in ("pendiente", "en_progreso", "bloqueada"):
            return
        ts = now_iso()
        resolver = row["assignee_id"] or row["created_by"]
        cycle = 1 + (c.execute("SELECT COUNT(*) FROM resolutions WHERE entity_type=? AND entity_id=?",
                               (etype, row["id"])).fetchone()[0])
        started = row.get("cycle_started_at") or row.get("started_at") or row["created_at"]
        c.execute("INSERT INTO resolutions(entity_type, entity_id, cycle, started_at, resolved_at, resolved_by, solution, "
                  "state) VALUES (?,?,?,?,?,?,?, 'pendiente')", (etype, row["id"], cycle, started, ts, resolver,
                                                                 f"{note}\n{url}"))
        c.execute(f"UPDATE {table} SET status='en_verificacion', verification_requested_at=?, verification_requested_by=?, "
                  f"updated_at=?, updated_by=?, row_version=row_version+1 WHERE id=?", (ts, resolver, ts, resolver, row["id"]))
        log_activity(db, c, None, etype, row["id"], row["key"], "github",
                     f"GitHub: {note} → {row['key']} pendiente de verificación", project_id=row["project_id"])
        perm = "incidencias.verificar_todas" if etype == "incident" else "tareas.verificar_todas"
        notify(db, c, {row["created_by"]} | set(users_with_perm(c, perm, row["project_id"])), "verify_request",
               f"{row['key']} lista para verificar", f"{note}: {row['title']}", f"gh-verify-{etype}-{row['id']}-{cycle}",
               etype, row["id"], row["key"])

    def poll(force: bool = False) -> dict:
        c = cfg()
        if not c.get("repo") or (not c.get("enabled") and not force):
            return {"skipped": True}
        if not force and c.get("last_poll"):
            last = datetime.strptime(c["last_poll"].replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S.%f%z")
            if datetime.now(timezone.utc) - last < timedelta(minutes=c.get("poll_minutes", 10)):
                return {"skipped": True}
        pattern = key_pattern()
        result = {"commits": 0, "pulls": 0, "links": 0, "transitions": 0}
        try:
            since = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
            commits = api(f"/commits?per_page=50&since={since}")
            pulls = api("/pulls?state=all&sort=updated&direction=desc&per_page=40")
        except urllib.error.HTTPError as exc:
            state["last_error"] = (f"GitHub respondió {exc.code}. Si el repositorio es privado, guarda un token de solo "
                                   "lectura en la bóveda, selecciónalo aquí y desbloquea la bóveda.")
            return {"error": state["last_error"]}
        except (urllib.error.URLError, OSError, ValueError) as exc:
            state["last_error"] = f"No se puede contactar con GitHub ({exc})."
            return {"error": state["last_error"]}
        with db.tx() as conn:
            for cm in commits:
                msg = (cm.get("commit") or {}).get("message", "")
                author = ((cm.get("commit") or {}).get("author") or {}).get("name", "")
                for etype, row in find_targets(msg, pattern):
                    if upsert_link(conn, etype, row, "commit", cm["sha"][:12], cm.get("html_url", ""),
                                   msg.splitlines()[0] if msg else "", "commit", author):
                        result["links"] += 1
                        db.emit(conn, etype, etype, row["id"])
                result["commits"] += 1
            for pr in pulls:
                st = "merged" if pr.get("merged_at") else pr.get("state", "open")
                text = f"{pr.get('title', '')}\n{pr.get('body') or ''}\n{(pr.get('head') or {}).get('ref', '')}"
                for etype, row in find_targets(text, pattern):
                    change = upsert_link(conn, etype, row, "pr", str(pr["number"]), pr.get("html_url", ""),
                                         pr.get("title", ""), st, (pr.get("user") or {}).get("login", ""))
                    if not change:
                        continue
                    result["links"] += 1
                    fresh = dict(conn.execute(f"SELECT * FROM {'incidents' if etype == 'incident' else 'tasks'} "
                                              f"WHERE id=?", (row["id"],)).fetchone())
                    if st == "open":
                        to_progress(conn, etype, fresh, f"PR #{pr['number']} abierta")
                    elif st == "merged":
                        to_verification(conn, etype, fresh, f"PR #{pr['number']} fusionada", pr.get("html_url", ""))
                        result["transitions"] += 1
                    db.emit(conn, etype, etype, row["id"])
                result["pulls"] += 1
            c["last_poll"] = now_iso()
            db.put_setting(conn, "github", c)
        state["last_error"] = ""
        state["last_result"] = {**result, "at": now_iso()}
        return result

    app.github_poll = poll
    _ = HttpError
