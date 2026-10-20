"""Los datos nunca se pierden: copias, restauración, modo recuperación y migración desde la versión 1."""
import shutil
import sqlite3
from pathlib import Path

from conftest import ADMIN_PASSWORD, Client, new_incident

from devcontrol.config import load_config
from devcontrol.server.backups import restore_files
from devcontrol.server.main import DB_NAME, ServerApp

V1_DB = Path(__file__).parent / "data" / "v1_ejemplo.db"


def start(layout, **kw):
    app = ServerApp(load_config(layout), layout, **kw)
    port = app.start(port=0, listen=["127.0.0.1"])
    app.base = f"http://127.0.0.1:{port}"
    return app


def test_backup_and_restore_keeps_previous_state(admin, server, layout):
    new_incident(admin, title="Antes de la copia")
    info = admin.post("/api/backups", {})
    name = (info.get("item") or info)["name"]
    assert (layout.backups_dir / name).exists()
    assert str(layout.backups_dir).startswith(str(layout.docs_root))   # en Documentos, fuera del programa
    new_incident(admin, title="Después de la copia")
    assert len(admin.get("/api/incidents")["items"]) == 2
    result = admin.post(f"/api/backups/{name}/restore", {"confirm": name})
    titles = [i["title"] for i in admin.get("/api/incidents")["items"]]
    assert titles == ["Antes de la copia"]
    # el estado anterior se guardó en otra copia antes de restaurar
    pre = result["previous_state_backup"]["name"]
    assert (layout.backups_dir / pre).exists()
    # los números ya usados no se repiten
    assert new_incident(admin)["key"] == "APP-003"


def test_shutdown_creates_closing_backup(layout):
    app = start(layout)
    anon = Client(app.base)
    anon.post("/api/setup", {"admin": {"username": "ana", "display_name": "Ana", "password": ADMIN_PASSWORD}})
    app.shutdown()
    assert any("-cierre.zip" in p.name for p in layout.backups_dir.iterdir())


def test_missing_database_with_backups_enters_recovery(layout):
    app = start(layout)
    anon = Client(app.base)
    anon.post("/api/setup", {"admin": {"username": "ana", "display_name": "Ana", "password": ADMIN_PASSWORD}})
    new_incident(anon.login("ana", ADMIN_PASSWORD))
    app.shutdown()                                      # deja una copia «cierre»
    data_dir = app.data_dir
    for p in data_dir.glob(DB_NAME + "*"):
        p.unlink()                                      # alguien borra la base de datos
    app = start(layout)
    try:
        anon = Client(app.base)
        health = anon.get("/api/health")
        assert health["recovery"] and not health["setup_required"]
        assert not (data_dir / DB_NAME).exists()        # no se ha creado una base vacía
        assert anon.call("POST", "/api/login", {"username": "ana", "password": ADMIN_PASSWORD})[0] == 404
        backups = anon.get("/api/recovery")["backups"]
        anon.post("/api/recovery/restore", {"name": backups[0]["name"]})
        admin = anon.login("ana", ADMIN_PASSWORD)
        assert [i["key"] for i in admin.get("/api/incidents")["items"]] == ["APP-001"] or \
            len(admin.get("/api/incidents")["items"]) == 1
    finally:
        app.shutdown(backup=False)


def test_recovery_start_empty_needs_confirmation(layout):
    app = start(layout)
    Client(app.base).post("/api/setup", {"admin": {"username": "ana", "display_name": "Ana", "password": ADMIN_PASSWORD}})
    app.shutdown()
    for p in app.data_dir.glob(DB_NAME + "*"):
        p.unlink()
    app = start(layout)
    try:
        anon = Client(app.base)
        assert anon.call("POST", "/api/recovery/start-empty", {"confirm": "si"})[0] == 400
        anon.post("/api/recovery/start-empty", {"confirm": "EMPEZAR VACIO"})
        assert anon.get("/api/health")["setup_required"]
    finally:
        app.shutdown(backup=False)


def test_migration_from_version_1(layout):
    cfg = load_config(layout)
    data_dir = cfg.server_data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(V1_DB, data_dir / DB_NAME)
    app = start(layout)
    try:
        # antes de migrar se hizo una copia
        assert any("previa-migracion" in p.name for p in layout.backups_dir.iterdir())
        owner = Client(app.base).login("claudio", "Clave-segura-1")
        cto = Client(app.base).login("julio", "Clave-segura-2")
        me = owner.get("/api/me")
        assert me["user"]["role_key"] == "admin" and cto.get("/api/me")["user"]["role_key"] == "cto"
        incidents = {i["key"]: i for i in owner.get("/api/incidents")["items"]}
        # El prefijo antiguo pasa al prefijo del proyecto (INC) y las referencias en los textos se actualizan
        assert sorted(incidents) == ["INC-001", "INC-002", "INC-003"]
        assert incidents["INC-001"]["status"] == "resuelta"
        assert incidents["INC-003"]["status"] == "bloqueada"
        assert len(owner.get("/api/tasks")["items"]) == 1
        bodies = " ".join(m["body"] for m in owner.get("/api/messages")["items"])
        assert "INC-001" in bodies and "ABC-001" not in bodies
        assert new_incident(owner)["key"] == "INC-004"
    finally:
        app.shutdown(backup=False)


def test_import_from_version_1_folder(layout, tmp_path):
    old = tmp_path / "programa-v1" / "datos"
    old.mkdir(parents=True)
    shutil.copy(V1_DB, old / "equipo.db")
    app = start(layout, import_from=old / "equipo.db")
    try:
        assert len(Client(app.base).login("claudio", "Clave-segura-1").get("/api/incidents")["items"]) == 3
        assert (old / "equipo.db").exists()               # el original no se toca
    finally:
        app.shutdown(backup=False)


def test_restore_files_moves_current_aside(layout, admin, server):
    new_incident(admin)
    name = admin.post("/api/backups", {})
    name = (name.get("item") or name)["name"]
    server.shutdown(backup=False)
    keep = restore_files(layout.backups_dir / name, server.data_dir, server.data_dir / DB_NAME, "prueba")
    assert (keep / DB_NAME).exists() and (server.data_dir / DB_NAME).exists()
    conn = sqlite3.connect(server.data_dir / DB_NAME)
    assert conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 1
    conn.close()


def test_installer_import_folder_is_detected(layout, tmp_path):
    from devcontrol.desktop import App
    old = tmp_path / "programa-v1"
    (old / "datos").mkdir(parents=True)
    shutil.copy(V1_DB, old / "datos" / "equipo.db")
    with open(layout.install_file, "a", encoding="utf-8") as fh:
        fh.write(f"importar_desde = {old}\n")
    app = App(layout, headless=True)
    assert app._import_path() == old / "datos" / "equipo.db"


def test_prepare_update_backs_up_with_counts(layout, admin, server):
    from devcontrol.desktop import App
    new_incident(admin)
    server.shutdown(backup=False)
    app = App(layout, headless=True)
    info = app.prepare_update()
    assert info["counts"]["incidents"] == 1 and (layout.backups_dir / info["backup"]).exists()
    assert "previa-actualizacion" in info["backup"]
