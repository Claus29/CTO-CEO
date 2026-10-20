"""Copias de seguridad de la base de datos y de los adjuntos, y restauración guiada."""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from .. import APP_NAME, APP_VERSION
from .common import clean_int, log_activity
from .db import Database, new_uid, now_iso
from .web import HttpError, Response, bad_request, not_found

NAME_RE = re.compile(r"^[a-z]+-copia-\d{8}-\d{6}(-\d+)?-[a-z-]+\.zip$")
KIND_LABELS = {"auto": "Automática", "manual": "Manual", "previa-restauracion": "Previa a una restauración",
               "cierre": "Al detener el servidor", "previa-actualizacion": "Previa a una actualización",
               "previa-migracion": "Previa a una migración de datos"}
PREFIX = "devcontrol"


class BackupError(Exception):
    pass


def db_member_of(names: set) -> str | None:
    """La base de datos está en la raíz del ZIP (datos.db, o el nombre que usaba la versión 1)."""
    dbs = sorted(n for n in names if n.endswith(".db") and "/" not in n)
    return dbs[0] if dbs else None


def restore_files(zip_path: Path, data_dir: Path, db_path: Path, reason: str) -> Path:
    """Sustituye la base de datos (y los adjuntos de la copia) con el servidor DETENIDO.

    El estado actual se aparta a restauraciones/<fecha>-<motivo>; nunca se borra nada.
    """
    zip_path, data_dir, db_path = Path(zip_path), Path(data_dir), Path(db_path)
    if not zip_path.exists():
        raise BackupError(f"No existe la copia {zip_path.name}.")
    keep_dir = data_dir / "restauraciones" / f"{datetime.now():%Y%m%d-%H%M%S}-{reason}"
    keep_dir.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            shutil.move(str(p), str(keep_dir / p.name))
    with zipfile.ZipFile(zip_path) as zf:
        member = db_member_of(set(zf.namelist()))
        if not member:
            raise BackupError("La copia no contiene una base de datos.")
        zf.extract(member, db_path.parent)
        if member != db_path.name:
            os.replace(db_path.parent / member, db_path)
        for n in zf.namelist():
            if n.startswith("adjuntos/") and not n.endswith("/") and ".." not in n.split("/"):
                if not (data_dir / n).exists():
                    zf.extract(n, data_dir)
    return keep_dir


class BackupManager:
    def __init__(self, db: Database | None, data_dir: Path, backups_dir: Path):
        self.db = db
        self.data_dir = Path(data_dir)
        self.backups_dir = Path(backups_dir)

    @property
    def dir(self) -> Path:
        return self.backups_dir

    @property
    def attachments_dir(self) -> Path:
        return self.data_dir / "adjuntos"

    def _path(self, name: str) -> Path:
        if not NAME_RE.match(name or ""):
            raise BackupError("Nombre de copia no válido.")
        p = self.dir / name
        if not p.exists():
            raise BackupError(f"No existe la copia {name}.")
        return p

    def create(self, kind: str = "manual", user: dict | None = None) -> dict:
        self.dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        name = f"{PREFIX}-copia-{stamp}-{kind}.zip"
        n = 2
        while (self.dir / name).exists():
            name = f"{PREFIX}-copia-{stamp}-{n}-{kind}.zip"
            n += 1
        tmp_dir = Path(tempfile.mkdtemp(prefix="copia-", dir=self.data_dir))
        try:
            db_copy = tmp_dir / "datos.db"
            with self.db.lock:
                dst = sqlite3.connect(str(db_copy))
                try:
                    self.db.conn.backup(dst)
                finally:
                    dst.close()
                counts = {t: self.db.scalar(f"SELECT COUNT(*) FROM {t}")
                          for t in ("incidents", "messages", "attachments", "tasks", "ideas", "activity")}
                attachment_files = [Path(r["stored_name"]) for r in
                                    self.db.query("SELECT stored_name FROM attachments")]
            manifest = {"app": APP_NAME, "app_version": APP_VERSION, "created_at": now_iso(),
                        "kind": kind, "created_by": user["display_name"] if user else "Sistema", "counts": counts}
            partial = self.dir / (name + ".parcial")
            with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
                zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
                zf.write(db_copy, "datos.db")
                missing = 0
                for stored in attachment_files:
                    src = self.attachments_dir / str(stored)[:2] / stored
                    if src.exists():
                        zf.write(src, f"adjuntos/{str(stored)[:2]}/{stored}", compress_type=zipfile.ZIP_STORED)
                    else:
                        missing += 1
                if missing:
                    manifest["missing_attachments"] = missing
            os.replace(partial, self.dir / name)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        return self.describe(self.dir / name)

    def describe(self, path: Path) -> dict:
        info = {"name": path.name, "size": path.stat().st_size, "created_at": None, "kind": None, "counts": {}}
        try:
            with zipfile.ZipFile(path) as zf:
                m = json.loads(zf.read("manifest.json").decode("utf-8"))
            info.update(created_at=m.get("created_at"), kind=m.get("kind"), counts=m.get("counts", {}),
                        created_by=m.get("created_by"))
        except (zipfile.BadZipFile, KeyError, json.JSONDecodeError, OSError):
            info["damaged"] = True
        info["kind_label"] = KIND_LABELS.get(info["kind"], "Desconocida")
        return info

    def list(self) -> list[dict]:
        if not self.dir.exists():
            return []
        items = [self.describe(p) for p in self.dir.glob("*-copia-*.zip") if NAME_RE.match(p.name)]
        items.sort(key=lambda i: i["name"], reverse=True)
        return items

    def last_auto_time(self) -> datetime | None:
        autos = sorted(re.search(r"\d{8}-\d{6}", p.name).group(0) for p in self.dir.glob("*-copia-*-auto.zip")) \
            if self.dir.exists() else []
        if not autos:
            return None
        return datetime.strptime(autos[-1], "%Y%m%d-%H%M%S")

    def apply_retention(self, keep: int) -> int:
        autos = sorted((p for p in self.dir.glob("*-copia-*-auto.zip")),
                       key=lambda p: re.search(r"\d{8}-\d{6}", p.name).group(0)) if self.dir.exists() else []
        removed = 0
        for p in autos[: max(0, len(autos) - max(1, keep))]:
            p.unlink()
            removed += 1
        return removed

    def delete(self, name: str) -> None:
        self._path(name).unlink()

    def validate(self, path: Path) -> dict:
        try:
            with zipfile.ZipFile(path) as zf:
                names = set(zf.namelist())
                db_member = db_member_of(names)
                if not db_member or "manifest.json" not in names:
                    raise BackupError("La copia está incompleta (falta la base de datos o el manifiesto).")
                bad = zf.testzip()
                if bad:
                    raise BackupError(f"La copia está dañada (archivo {bad}).")
                for n in names:
                    if n.startswith("/") or ".." in n.split("/"):
                        raise BackupError("La copia contiene rutas no válidas.")
                tmp_dir = Path(tempfile.mkdtemp(prefix="validar-", dir=self.data_dir))
                try:
                    zf.extract(db_member, tmp_dir)
                    conn = sqlite3.connect(str(tmp_dir / db_member))
                    try:
                        ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
                        conn.execute("SELECT COUNT(*) FROM incidents").fetchone()
                    finally:
                        conn.close()
                finally:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                if ok != "ok":
                    raise BackupError("La base de datos de la copia no supera la comprobación de integridad.")
        except zipfile.BadZipFile:
            raise BackupError("El archivo no es una copia de seguridad válida (ZIP dañado).")
        except sqlite3.DatabaseError as exc:
            raise BackupError(f"La base de datos de la copia no es válida: {exc}")
        return self.describe(path)

    def restore(self, name: str, user: dict | None = None) -> dict:
        """Restaura una copia. Antes guarda el estado actual en una copia previa."""
        path = self._path(name)
        info = self.validate(path)
        pre = self.create("previa-restauracion", user)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        keep_dir = self.data_dir / "restauraciones" / f"{stamp}-estado-anterior"
        with self.db.lock:
            self.db.close()
            try:
                keep_dir.mkdir(parents=True, exist_ok=True)
                db_path = self.db.path
                for suffix in ("", "-wal", "-shm"):
                    p = Path(str(db_path) + suffix)
                    if p.exists():
                        shutil.move(str(p), str(keep_dir / p.name))
                if self.attachments_dir.exists():
                    shutil.move(str(self.attachments_dir), str(keep_dir / "adjuntos"))
                with zipfile.ZipFile(path) as zf:
                    member = db_member_of(set(zf.namelist()))
                    zf.extract(member, db_path.parent)
                    if member != db_path.name:
                        os.replace(db_path.parent / member, db_path)
                    for n in zf.namelist():
                        if n.startswith("adjuntos/") and not n.endswith("/"):
                            zf.extract(n, self.data_dir)
                self.attachments_dir.mkdir(parents=True, exist_ok=True)
            except Exception:
                # Si algo falla, se recupera el estado anterior tal cual estaba.
                for p in keep_dir.iterdir() if keep_dir.exists() else []:
                    target = (self.data_dir / "adjuntos") if p.name == "adjuntos" else db_path.parent / p.name
                    if target.exists():
                        shutil.rmtree(target) if target.is_dir() else target.unlink()
                    shutil.move(str(p), str(target))
                self.db.open()
                raise
            self.db.open()
            # Se conservan las sesiones abiertas para no expulsar a nadie.
            old_db = keep_dir / db_path.name
            if old_db.exists():
                c = self.db.conn
                c.execute("ATTACH DATABASE ? AS anterior", (str(old_db),))
                try:
                    c.execute("INSERT OR IGNORE INTO sessions SELECT * FROM anterior.sessions "
                              "WHERE user_id IN (SELECT id FROM users)")
                    # Los contadores nunca retroceden: un ID ya usado no se vuelve a asignar.
                    c.execute("INSERT INTO counters(name, value) SELECT name, value FROM anterior.counters WHERE true "
                              "ON CONFLICT(name) DO UPDATE SET value = MAX(value, excluded.value)")
                finally:
                    c.execute("DETACH DATABASE anterior")
            with self.db.tx() as c:
                c.execute("UPDATE meta SET value=? WHERE key='epoch'", (new_uid(),))
                log_activity(self.db, c, user if user and c.execute("SELECT 1 FROM users WHERE id=?",
                                                                    (user["id"],)).fetchone() else None,
                             "backup", None, name, "restored",
                             f"Restauró la copia de seguridad {name} (el estado anterior se guardó en {pre['name']})")
                self.db.emit(c, "reset")
            self.db.epoch = self.db.meta("epoch")
        return {"restored": info, "previous_state_backup": pre}


def register(router, app):
    db = app.db
    mgr: BackupManager = app.backups

    @router.route("GET", "/api/backups", perm="sistema.copias")
    def list_backups(req):
        s = db.all_settings()
        return {"items": mgr.list(), "folder": str(mgr.dir),
                "settings": {"auto_backup_hours": s["auto_backup_hours"], "backup_keep": s["backup_keep"]}}

    @router.route("POST", "/api/backups", perm="sistema.copias")
    def create_backup(req):
        info = mgr.create("manual", req.user)
        with db.tx() as c:
            log_activity(db, c, req.user, "backup", None, info["name"], "created",
                         f"Creó una copia de seguridad manual ({info['name']})")
        return Response.json({"item": info}, 201)

    @router.route("POST", "/api/backups/{name}/restore", perm="sistema.copias")
    def restore(req, name):
        if req.json.get("confirm") != name:
            raise bad_request("Para restaurar debes confirmar escribiendo el nombre exacto de la copia.",
                              "confirmacion_requerida")
        try:
            return mgr.restore(name, req.user)
        except BackupError as exc:
            raise HttpError(400, "copia_invalida", str(exc))

    @router.route("GET", "/api/backups/{name}/download", perm="sistema.copias")
    def download(req, name):
        try:
            path = mgr._path(name)
        except BackupError as exc:
            raise not_found(str(exc))
        return Response.file(path, name, "application/zip")

    @router.route("DELETE", "/api/backups/{name}", perm="sistema.copias")
    def delete(req, name):
        try:
            mgr.delete(name)
        except BackupError as exc:
            raise not_found(str(exc))
        with db.tx() as c:
            log_activity(db, c, req.user, "backup", None, name, "deleted", f"Eliminó la copia de seguridad {name}")
        return {"ok": True}

    @router.route("GET", "/api/settings")
    def get_settings(req):
        settings = db.all_settings()
        settings.pop("vault", None)
        settings.pop("github", None)
        return {"settings": settings}

    INT_RULES = {"auto_backup_hours": (0, 24 * 30), "backup_keep": (1, 1000), "stale_days": (0, 365),
                 "due_soon_days": (0, 60), "daily_summary_hour": (0, 23), "weekly_summary_weekday": (0, 6),
                 "standup_hour": (0, 23), "password_min_length": (8, 64)}
    LABELS = {"auto_backup_hours": "Copia automática cada (horas)", "backup_keep": "Copias automáticas a conservar",
              "stale_days": "Días para considerar estancada", "due_soon_days": "Días de aviso antes de vencer",
              "daily_summary_hour": "Hora del resumen diario", "weekly_summary_weekday": "Día del resumen semanal",
              "standup_hour": "Hora del daily", "password_min_length": "Longitud mínima de contraseña",
              "sla_enabled": "SLA activados", "verification_required": "Verificación por el creador",
              "standup_enabled": "Daily asíncrono", "sla": "Plazos por prioridad (SLA)",
              "report_templates": "Plantillas de reporte", "standup_weekdays": "Días del daily",
              "wip_limits": "Límites de trabajo en curso", "require_2fa_roles": "Roles con verificación en dos pasos"}

    @router.route("PUT", "/api/settings")
    def put_settings(req):
        data = req.json
        keys = set(data)
        if keys & {"auto_backup_hours", "backup_keep"}:
            from . import auth
            auth.require_any(req.user, "sistema.copias", "sistema.ajustes")
            if keys - {"auto_backup_hours", "backup_keep"}:
                auth.require(req.user, "sistema.ajustes")
        else:
            from . import auth
            auth.require(req.user, "sistema.ajustes")
        from .common import CATEGORIES, PRIORITIES, REPORT_FIELDS
        changes = {}
        for k, (lo, hi) in INT_RULES.items():
            if k in data:
                changes[k] = clean_int(data[k], LABELS[k], lo, hi)
        for k in ("sla_enabled", "verification_required", "standup_enabled"):
            if k in data:
                changes[k] = bool(data[k])
        if "standup_weekdays" in data:
            changes["standup_weekdays"] = sorted({clean_int(d, "día", 0, 6) for d in data["standup_weekdays"] or []})
        if "sla" in data:
            sla = {}
            for p in PRIORITIES:
                rule = (data["sla"] or {}).get(p) or {}
                sla[p] = {"response_hours": clean_int(rule.get("response_hours") or 0, "horas de respuesta", 0, 10000),
                          "resolve_hours": clean_int(rule.get("resolve_hours") or 0, "horas de resolución", 0, 100000)}
            changes["sla"] = sla
        if "report_templates" in data:
            tpls = {}
            for cat in CATEGORIES:
                t = (data["report_templates"] or {}).get(cat) or {}
                tpls[cat] = {"required": [f for f in t.get("required") or [] if f in REPORT_FIELDS],
                             "recommended": [f for f in t.get("recommended") or [] if f in REPORT_FIELDS]}
            changes["report_templates"] = tpls
        if "wip_limits" in data:
            changes["wip_limits"] = {k: clean_int(v, "límite", 0, 999) for k, v in (data["wip_limits"] or {}).items()
                                     if k in ("pendiente", "en_progreso", "bloqueada", "en_verificacion") and v not in (None, "")}
        if "require_2fa_roles" in data:
            keys_ok = {r["key"] for r in db.query("SELECT key FROM roles")}
            changes["require_2fa_roles"] = [r for r in data["require_2fa_roles"] or [] if r in keys_ok]
        with db.tx() as c:
            for k, v in changes.items():
                old = db.get_setting(k)
                if v != old:
                    db.put_setting(c, k, v)
                    shown = f"{old} → {v}" if not isinstance(v, (dict, list)) else "actualizado"
                    log_activity(db, c, req.user, "settings", None, k, "changed", f"Cambió el ajuste «{LABELS[k]}»: {shown}")
        app.settings_invalidate()
        return get_settings(req)


def run_auto_backup(app) -> None:
    hours = int(app.db.get_setting("auto_backup_hours"))
    if hours <= 0:
        return
    last = app.backups.last_auto_time()
    if last is None or datetime.now() - last >= timedelta(hours=hours):
        info = app.backups.create("auto")
        app.backups.apply_retention(int(app.db.get_setting("backup_keep")))
        app.log.info("Copia automática creada: %s", info["name"])
        with app.db.tx() as c:
            log_activity(app.db, c, None, "backup", None, info["name"], "created",
                         f"Copia de seguridad automática creada ({info['name']})")
