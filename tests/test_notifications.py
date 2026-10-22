"""Avisos 2.2: quién se entera de cada cambio, sin duplicados, sin avisarte de lo tuyo y respetando preferencias."""
import uuid

from conftest import create_user


def project_id(client):
    return client.get("/api/me")["projects"][0]["id"]


def notes(client, **filters):
    items = client.get("/api/notifications?limit=200")["items"]
    return [n for n in items if all(n.get(k) == v for k, v in filters.items())]


def new_task(client, **extra):
    body = {"uid": uuid.uuid4().hex, "title": "Pantalla de inicio", "description": "", "project_id": project_id(client),
            **extra}
    return client.post("/api/tasks", body)["item"]


def team(admin):
    pid = project_id(admin)
    luis = create_user(admin, "luis", "dev", project_ids=[pid])
    marta = create_user(admin, "marta", "dev", project_ids=[pid])
    return luis, marta


def test_new_task_notifies_project_but_not_the_author(admin):
    luis, marta = team(admin)
    t = new_task(luis)
    got = notes(marta, type="task_created", entity_id=t["id"])
    assert len(got) == 1 and "Luis" in got[0]["body"] and t["key"] in got[0]["title"]
    assert notes(luis, type="task_created") == []          # nunca se avisa a quien lo hace
    assert notes(admin, type="task_created", entity_id=t["id"])


def test_assignee_gets_one_notice_not_two(admin):
    luis, marta = team(admin)
    me_marta = marta.get("/api/me")["user"]["id"]
    t = new_task(admin, assignee_id=me_marta)
    mine = notes(marta, entity_id=t["id"], entity_type="task")
    assert [n["type"] for n in mine] == ["assigned"]


def test_edits_are_grouped_and_reach_creator_assignee_and_followers(admin):
    luis, marta = team(admin)
    me_marta = marta.get("/api/me")["user"]["id"]
    t = new_task(admin, assignee_id=me_marta)
    # Luis sigue la tarea; cambia Marta dos cosas seguidas
    luis.post("/api/follows", {"entity_type": "task", "entity_id": t["id"]})
    t = marta.patch(f"/api/tasks/{t['id']}", {"priority": "alta", "version": t["version"]})["item"]
    marta.patch(f"/api/tasks/{t['id']}", {"title": "Pantalla de inicio nueva", "version": t["version"]})
    for who in (luis, admin):
        upd = notes(who, type="task_updated", entity_id=t["id"])
        assert len(upd) == 1, upd                          # dos ediciones seguidas → un solo aviso
        assert "Marta cambió prioridad" in upd[0]["body"] and "título" in upd[0]["body"]
    assert notes(marta, type="task_updated") == []


def test_status_changes_and_resolution_reach_followers(admin):
    luis, marta = team(admin)
    t = new_task(luis)
    marta.post("/api/follows", {"entity_type": "task", "entity_id": t["id"]})
    t = admin.patch(f"/api/tasks/{t['id']}", {"status": "en_progreso", "version": t["version"]})["item"]
    st = notes(marta, type="status_changed", entity_id=t["id"])
    assert st and "inició" in st[0]["body"].lower()
    assert notes(luis, type="status_changed", entity_id=t["id"])      # el creador también


def test_follow_whole_project_and_task_scope(admin):
    luis, marta = team(admin)
    pid = project_id(admin)
    # Marta solo quiere lo suyo: no recibe tareas nuevas del proyecto…
    marta.ok("PUT", "/api/notifications/prefs", {"settings": {"task_scope": "mio"}})
    t = new_task(luis)
    assert notes(marta, type="task_created") == []
    # …salvo que siga el proyecto entero
    marta.post("/api/follows", {"entity_type": "project", "entity_id": pid})
    t2 = new_task(luis)
    assert notes(marta, type="task_created", entity_id=t2["id"])
    assert notes(marta, type="task_created", entity_id=t["id"]) == []


def test_preferences_presets_and_in_app_off(admin):
    luis, marta = team(admin)
    prefs = marta.ok("PUT", "/api/notifications/prefs", {"preset": "solo_mio"})
    by_type = {i["type"]: i for i in prefs["items"]}
    assert by_type["assigned"]["desktop"] and not by_type["task_created"]["desktop"]
    assert prefs["settings"]["task_scope"] == "mio" and prefs["areas"]
    marta.ok("PUT", "/api/notifications/prefs", {"preset": "todo",
                                                  "items": [{"type": "task_created", "in_app": False,
                                                             "desktop": False}],
                                                  "settings": {"quiet_from": "22:00", "quiet_to": "07:30"}})
    new_task(luis)
    assert notes(marta, type="task_created") == []
    assert marta.get("/api/notifications/prefs")["settings"]["quiet_from"] == "22:00"
    code, _ = marta.call("PUT", "/api/notifications/prefs", {"settings": {"quiet_from": "25:00"}})
    assert code == 400


def test_follow_requires_access(admin):
    luis, _ = team(admin)
    code, _ = luis.call("POST", "/api/follows", {"entity_type": "task", "entity_id": 999})
    assert code == 404
    code, _ = luis.call("POST", "/api/follows", {"entity_type": "usuario", "entity_id": 1})
    assert code == 400


def test_chat_replies_and_channel_modes(admin, server):
    luis, marta = team(admin)
    ch = admin.get("/api/channels")["items"][0]["id"]
    first = marta.post("/api/messages", {"uid": uuid.uuid4().hex, "body": "¿Subimos la beta hoy?", "channel_id": ch})
    # Luis responde a Marta: ella recibe «ha respondido a tu mensaje», no un «mensaje nuevo» más
    luis.post("/api/messages", {"uid": uuid.uuid4().hex, "body": "Sí, a las 18:00", "channel_id": ch,
                                "reply_to_id": first["id"]})
    mine = notes(marta, entity_type="message")
    assert [n["type"] for n in mine] == ["chat_reply"] and "Luis" in mine[0]["title"]
    assert notes(admin, type="message_new")
    # Ana pone el canal en «Solo menciones»: deja de recibir mensajes normales, pero sí menciones
    admin.ok("PUT", f"/api/channels/{ch}/notify", {"mode": "menciones"})
    before = len(notes(admin, type="message_new"))
    luis.post("/api/messages", {"uid": uuid.uuid4().hex, "body": "Otra cosa", "channel_id": ch})
    assert len(notes(admin, type="message_new")) == before
    luis.post("/api/messages", {"uid": uuid.uuid4().hex, "body": "@ana mira esto", "channel_id": ch})
    assert notes(admin, type="mention")
    assert admin.get(f"/api/channels/{ch}/notify")["mode"] == "menciones"


def test_account_notices(admin, server):
    luis, _ = team(admin)
    me = luis.get("/api/me")["user"]["id"]
    rid = {r["key"]: r["id"] for r in admin.get("/api/admin/roles")["items"]}["qa"]
    admin.patch(f"/api/admin/users/{me}", {"role_id": rid})
    assert notes(luis, type="account")[0]["title"] == "Tu rol ha cambiado"
    # entrar desde un equipo nuevo avisa (el primero no, para no avisar al estrenar la cuenta)
    from conftest import Client
    pw = "Secreta-Nueva-1"
    admin.post("/api/admin/users", {"username": "pepe", "display_name": "Pepe", "password": pw, "role_id": rid,
                                    "must_change_password": False})
    for label in ("PORTATIL-PEPE", "PORTATIL-PEPE", "PC-DESCONOCIDO"):
        token = Client(server.base).ok("POST", "/api/login", {"username": "pepe", "password": pw,
                                                               "client_label": label})["token"]
    fresh = Client(server.base, token)
    titles = [n["title"] for n in notes(fresh, type="account")]
    assert sum("Nuevo inicio de sesión" in t for t in titles) == 1 and any("PC-DESCONOCIDO" in t for t in titles)


def test_scheduled_digest_and_verification_reminders(admin, server):
    from datetime import datetime
    luis, marta = team(admin)
    me_marta = marta.get("/api/me")["user"]["id"]
    t = new_task(admin, assignee_id=me_marta, target_date="2026-10-19")
    monday_10 = datetime(2026, 10, 19, 10, 0)
    server.jobs.pending_digest(monday_10)
    server.jobs.pending_digest(monday_10)          # una sola vez al día
    dig = notes(marta, type="pending_digest")
    assert len(dig) == 1 and "Tienes asignada 1 tarea" in dig[0]["body"] and "vence hoy" in dig[0]["body"]
    # en verificación desde hace dos días → recordatorio a quien la creó (Ana)
    marta.patch(f"/api/tasks/{t['id']}", {"status": "resuelto", "version": t["version"]})
    with server.db.tx() as c:
        c.execute("UPDATE tasks SET verification_requested_at='2020-01-01T00:00:00.000Z' WHERE id=?", (t["id"],))
    server.jobs.verify_waiting(monday_10)
    assert notes(admin, type="verify_waiting", entity_id=t["id"])
    # sábado: no hay resumen
    server.jobs.pending_digest(datetime(2026, 10, 24, 10, 0))
    assert len(notes(marta, type="pending_digest")) == 1


def test_published_version_reaches_the_team_once(admin):
    luis, marta = team(admin)
    vid = admin.post("/api/versions", {"uid": uuid.uuid4().hex, "name": "1.4", "project_id": project_id(admin)})["id"]
    v = next(x for x in admin.get("/api/versions")["items"] if x["id"] == vid)
    assert notes(marta, type="release", entity_id=v["id"])           # nueva versión planificada
    admin.patch(f"/api/versions/{v['id']}", {"status": "publicada", "version": v["version"]})
    pub = [n for n in notes(marta, type="release") if "publicada" in n["title"]]
    assert len(pub) == 1


def test_long_descriptions_up_to_500k(admin):
    """2.2: las descripciones de tareas e incidencias admiten hasta 500 000 caracteres."""
    long_text = ("Paso con acentos y ñ: revisar el flujo de pago. " * 11000)[:500_000]
    t = new_task(admin, description=long_text)
    full = admin.get(f"/api/tasks/{t['id']}")["item"]
    assert len(full["description"]) == 500_000
    listed = next(x for x in admin.get("/api/tasks")["items"] if x["id"] == t["id"])
    assert listed["description_truncated"] and len(listed["description"]) < 2000     # el listado sigue siendo ligero
    t = admin.patch(f"/api/tasks/{t['id']}", {"description": long_text[:-1] + "!", "version": t["version"]})["item"]
    assert admin.get(f"/api/tasks/{t['id']}")["item"]["description"].endswith("!")
    code, err = admin.call("POST", "/api/tasks", {"uid": uuid.uuid4().hex, "title": "Demasiado", "project_id":
                                                  project_id(admin), "description": "x" * 500_001})
    assert code == 400 and "500000" in err["message"]
