"""Archivos adjuntos: almacenamiento persistente y compartido en el anfitrión."""
from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import secrets
import shutil
import tempfile
import zipfile
from pathlib import Path

from . import auth
from .common import clean_uid, log_activity
from .db import now_iso
from .entities import find_entity
from .web import HttpError, Response, bad_request, not_found

INLINE_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "video/mp4", "video/webm",
                "video/quicktime", "audio/mpeg", "audio/wav", "application/pdf", "text/plain")

mimetypes.add_type("video/mp4", ".mp4")
mimetypes.add_type("video/webm", ".webm")
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("video/quicktime", ".mov")


def safe_filename(name: str) -> str:
    name = (name or "").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\x00-\x1f<>:\"|?*]", "_", name).strip(" .")
    if not name:
        raise bad_request("El archivo no tiene un nombre válido.", "nombre_invalido")
    if len(name) > 180:
        stem, ext = os.path.splitext(name)
        name = stem[: 180 - len(ext)] + ext
    return name


class AttachmentStore:
    def __init__(self, app):
        self.app = app
        self.db = app.db

    @property
    def root(self) -> Path:
        return self.app.data_dir / "adjuntos"

    def path_for(self, stored_name: str) -> Path:
        return self.root / stored_name[:2] / stored_name

    def list_for(self, owner_type: str, owner_id: int) -> list[dict]:
        rows = self.db.query("SELECT a.*, u.display_name AS uploaded_by_name FROM attachments a "
                             "LEFT JOIN users u ON u.id = a.uploaded_by "
                             "WHERE owner_type=? AND owner_id=? ORDER BY a.id", (owner_type, owner_id))
        for r in rows:
            r.pop("stored_name", None)
        return rows

    def list_for_many(self, owner_type: str, ids: list[int]) -> dict[int, list[dict]]:
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        out: dict[int, list[dict]] = {}
        for r in self.db.query(f"SELECT id, uid, owner_id, filename, size, content_type, created_at FROM attachments "
                               f"WHERE owner_type=? AND owner_id IN ({marks}) ORDER BY id", [owner_type] + ids):
            out.setdefault(r["owner_id"], []).append(r)
        return out

    def store(self, upload_path: Path) -> tuple[str, str, int]:
        stored = secrets.token_hex(16)
        dest = self.path_for(stored)
        dest.parent.mkdir(parents=True, exist_ok=True)
        h = hashlib.sha256()
        size = 0
        with open(upload_path, "rb") as src:
            for chunk in iter(lambda: src.read(1024 * 1024), b""):
                h.update(chunk)
                size += len(chunk)
        shutil.move(str(upload_path), str(dest))
        try:
            with open(dest, "rb") as fh:
                os.fsync(fh.fileno())
        except OSError:
            pass
        return stored, h.hexdigest(), size


def register(router, app):
    db = app.db
    store = app.attachments

    def resolve_owner(req) -> tuple[str, int, str | None, int | None]:
        owner_type = req.q("owner_type")
        owner_uid = req.q("owner_uid")
        owner_id = req.q("owner_id")
        user = req.user
        if owner_type in ("incident", "task"):
            auth.require(user, "incidencias.adjuntar" if owner_type == "incident" else "tareas.ver")
            if owner_type == "task":
                auth.require_any(user, "tareas.trabajar", "tareas.crear", "tareas.editar")
            table = "incidents" if owner_type == "incident" else "tasks"
            if owner_uid:
                row = db.query_one(f"SELECT id FROM {table} WHERE uid=?", (owner_uid,))
                if not row:
                    raise not_found("El elemento al que se adjunta el archivo todavía no existe en el servidor.")
                owner_id = row["id"]
            row = find_entity(db, user, owner_type, owner_id or "")
            return owner_type, row["id"], row["key"], row["project_id"]
        if owner_type == "message":
            auth.require(user, "chat.usar")
            row = db.query_one("SELECT id, user_id FROM messages WHERE uid=?", (owner_uid,)) if owner_uid else \
                db.query_one("SELECT id, user_id FROM messages WHERE id=?", (owner_id,))
            if not row:
                raise not_found("El mensaje al que se adjunta el archivo todavía no existe en el servidor.")
            if row["user_id"] != user["id"]:
                raise HttpError(403, "sin_permiso", "Solo puedes adjuntar archivos a tus propios mensajes.")
            return owner_type, row["id"], None, None
        if owner_type == "wiki":
            auth.require(user, "conocimiento.editar")
            row = find_entity(db, user, "wiki", owner_id or "")
            return owner_type, row["id"], row["title"], row["project_id"]
        raise bad_request("Tipo de destino del adjunto no válido.")

    @router.route("POST", "/api/uploads", upload=True, idempotent=True)
    def upload(req):
        if req.upload_path is None:
            raise bad_request("No se ha recibido ningún archivo.")
        filename = safe_filename(req.q("filename"))
        ext = os.path.splitext(filename)[1].lower().lstrip(".")
        if ext in app.cfg.blocked_extensions:
            raise HttpError(415, "tipo_no_permitido",
                            f"Por seguridad no se aceptan archivos .{ext}. Comprímelo en un ZIP si necesitas enviarlo.")
        size = req.upload_path.stat().st_size
        if size == 0:
            raise HttpError(400, "archivo_vacio", f"El archivo «{filename}» está vacío.")
        uid = clean_uid(req.q("uid")) if req.q("uid") else None
        if uid:
            dup = db.query_one("SELECT * FROM attachments WHERE uid=?", (uid,))
            if dup:
                dup.pop("stored_name", None)
                return {"item": dup, "duplicate": True}
        owner_type, owner_id, owner_key, project_id = resolve_owner(req)
        ctype = (req.headers.get("content-type") or "").split(";")[0].strip().lower()
        if not re.match(r"^[a-z0-9.+-]+/[a-z0-9.+-]+$", ctype) or ctype == "application/octet-stream":
            ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        stored, sha, size = store.store(req.upload_path)
        user = req.user
        try:
            with db.tx() as c:
                cur = c.execute(
                    "INSERT INTO attachments(uid, owner_type, owner_id, filename, stored_name, size, content_type, "
                    "sha256, uploaded_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (uid, owner_type, owner_id, filename, stored, size, ctype, sha, user["id"], now_iso()))
                aid = cur.lastrowid
                if owner_type in ("incident", "task"):
                    table = "incidents" if owner_type == "incident" else "tasks"
                    c.execute(f"UPDATE {table} SET updated_at=?, updated_by=? WHERE id=?",
                              (now_iso(), user["id"], owner_id))
                    log_activity(db, c, user, owner_type, owner_id, owner_key, "attachment",
                                 f"Adjuntó «{filename}» a {owner_key}", {"attachment_id": aid, "size": size}, project_id)
                    db.emit(c, owner_type, owner_type, owner_id, actor_id=user["id"])
                elif owner_type == "wiki":
                    db.emit(c, "wiki", "wiki", owner_id, actor_id=user["id"])
                else:
                    db.emit(c, "message", "message", owner_id, actor_id=user["id"])
        except Exception:
            try:
                store.path_for(stored).unlink()
            except OSError:
                pass
            raise
        item = db.query_one("SELECT * FROM attachments WHERE id=?", (aid,))
        item.pop("stored_name", None)
        return Response.json({"item": item}, 201)

    def check_attachment_access(user, row):
        if row["owner_type"] in ("incident", "task", "wiki"):
            find_entity(db, user, row["owner_type"], row["owner_id"])
        elif row["owner_type"] == "message":
            auth.require(user, "chat.usar")

    @router.route("GET", "/api/attachments/{aid}")
    def download(req, aid):
        row = db.query_one("SELECT * FROM attachments WHERE id=?", (aid,))
        if not row:
            raise not_found("El archivo adjunto no existe.")
        check_attachment_access(req.user, row)
        path = store.path_for(row["stored_name"])
        if not path.exists():
            raise HttpError(410, "archivo_perdido",
                            "El archivo no se encuentra en el almacenamiento del anfitrión. Restaura una copia de "
                            "seguridad si es necesario.")
        inline = req.q("inline") == "1" and row["content_type"] in INLINE_TYPES
        return Response.file(path, row["filename"], row["content_type"], inline=inline)

    @router.route("GET", "/api/{etype}/{ident}/attachments.zip")
    def download_zip(req, etype, ident):
        owner_type = {"incidents": "incident", "tasks": "task"}.get(etype)
        if not owner_type:
            raise not_found()
        inc = find_entity(db, req.user, owner_type, ident)
        rows = db.query("SELECT * FROM attachments WHERE owner_type=? AND owner_id=? ORDER BY id",
                        (owner_type, inc["id"]))
        if not rows:
            raise not_found(f"{inc['key']} no tiene archivos adjuntos.")
        tmp_dir = app.data_dir / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=tmp_dir, suffix=".zip")
        os.close(fd)
        used = set()
        with zipfile.ZipFile(tmp_name, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for r in rows:
                src = store.path_for(r["stored_name"])
                if not src.exists():
                    continue
                name = r["filename"]
                stem, ext = os.path.splitext(name)
                n = 2
                while name.lower() in used:
                    name = f"{stem} ({n}){ext}"
                    n += 1
                used.add(name.lower())
                compress = zipfile.ZIP_STORED if r["content_type"].startswith(("image/", "video/")) \
                    else zipfile.ZIP_DEFLATED
                zf.write(src, name, compress_type=compress)
        return Response.file(Path(tmp_name), f"{inc['key']}-adjuntos.zip", "application/zip", delete_after=True)
