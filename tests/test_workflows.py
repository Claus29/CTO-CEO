"""Flujos de trabajo: verificación por quien creó la incidencia, permisos por rol, proyectos y actividad."""
import uuid

from conftest import create_user, new_incident


def notif_types(client):
    return [n["type"] for n in client.get("/api/notifications")["items"]]


def test_incident_needs_verification_by_creator(admin):
    dev = create_user(admin, "dani", "dev")
    inc = new_incident(admin)
    assert inc["key"] == "APP-001" and inc["status"] == "pendiente"
    dev_id = dev.get("/api/me")["user"]["id"]
    admin.patch(f"/api/incidents/{inc['key']}", {"assignee_id": dev_id, "version": inc["version"]})
    assert "assigned" in notif_types(dev)
    dev.post(f"/api/incidents/{inc['key']}/status", {"status": "en_progreso"})
    item = dev.post(f"/api/incidents/{inc['key']}/status", {"status": "resuelta", "solution": "Arreglado"})["item"]
    assert item["status"] == "en_verificacion"           # no cuenta como resuelta todavía
    assert "verify_request" in notif_types(admin)
    # el desarrollador no puede verificar su propio trabajo
    code, _ = dev.call("POST", f"/api/incidents/{inc['key']}/verify", {"accept": True})
    assert code == 403
    # rechazo con motivo: vuelve a estar en progreso y se avisa a quien la resolvió
    admin.post(f"/api/incidents/{inc['key']}/verify", {"accept": False, "reason": "Sigue fallando en Android"})
    detail = dev.get(f"/api/incidents/{inc['key']}")
    assert detail["item"]["status"] in ("en_progreso", "pendiente")
    assert "rejected" in notif_types(dev)
    dev.post(f"/api/incidents/{inc['key']}/status", {"status": "resuelta", "solution": "Ahora sí"})
    admin.post(f"/api/incidents/{inc['key']}/verify", {"accept": True})
    detail = admin.get(f"/api/incidents/{inc['key']}")
    assert detail["item"]["status"] == "resuelta"
    states = [r["state"] for r in detail["item"]["resolutions"]]
    assert states.count("rechazada") == 1 and states.count("verificada") == 1
    assert "verified" in notif_types(dev)
    # reabrir con motivo
    admin.post(f"/api/incidents/{inc['key']}/status", {"status": "pendiente", "reopen_reason": "Vuelve a pasar"})
    assert admin.get(f"/api/incidents/{inc['key']}")["item"]["reopen_count"] == 1


def test_creator_resolving_own_incident_skips_verification(admin):
    inc = new_incident(admin)
    item = admin.post(f"/api/incidents/{inc['key']}/status", {"status": "resuelta", "solution": "Era configuración"})["item"]
    assert item["status"] == "resuelta"


def test_report_template_required_fields(admin):
    code, err = admin.call("POST", "/api/incidents", {"uid": uuid.uuid4().hex, "title": "Falla algo", "priority": "media",
                                                       "category": "errores"})
    assert code == 400 and err["error"] == "reporte_incompleto" and "steps" in err["missing"]
    new_incident(admin, steps="", na_fields={"steps": "Ocurre al azar"})


def test_roles_limit_actions(admin):
    observer = create_user(admin, "olga", "observer")
    code, _ = observer.call("POST", "/api/incidents", {"uid": uuid.uuid4().hex, "title": "x", "priority": "baja",
                                                        "category": "diseno"})
    assert code == 403
    assert observer.call("GET", "/api/admin/users")[0] == 403
    assert observer.call("GET", "/api/incidents")[0] == 200
    qa = create_user(admin, "quim", "qa")
    inc = new_incident(qa)
    assert inc["created_by_name"] == "Quim"


def test_custom_role_and_permissions(admin):
    rid = admin.post("/api/admin/roles", {"name": "Diseño", "perms": ["incidencias.ver", "chat.usar"]})["id"]
    pw = "Secreta-" + uuid.uuid4().hex[:8]
    admin.post("/api/admin/users", {"username": "dia", "display_name": "Dia", "password": pw, "role_id": rid,
                                    "must_change_password": False})
    dia = admin.login("dia", pw)
    assert sorted(dia.get("/api/me")["permissions"]) == ["chat.usar", "incidencias.ver"]
    assert dia.call("GET", "/api/tasks")[0] == 403
    admin.patch(f"/api/admin/roles/{rid}", {"perms": ["incidencias.ver", "chat.usar", "tareas.ver"]})
    assert dia.call("GET", "/api/tasks")[0] == 200       # el cambio de permisos se aplica al momento
    # no se puede dejar el sistema sin administradores
    me = admin.get("/api/me")["user"]
    code, _ = admin.call("PATCH", f"/api/admin/users/{me['id']}", {"role_id": rid})
    assert code == 403


def test_project_visibility(admin):
    p2 = admin.post("/api/projects", {"key": "WEB", "name": "Web", "uid": uuid.uuid4().hex})["id"]
    dev = create_user(admin, "pepe", "dev")
    pepe_id = dev.get("/api/me")["user"]["id"]
    admin.patch(f"/api/admin/users/{pepe_id}", {"project_ids": [p2]})
    secret = new_incident(admin)                      # en APP, donde Pepe no está
    web = new_incident(admin, project_id=p2)
    assert web["key"] == "WEB-001"
    keys = [i["key"] for i in dev.get("/api/incidents")["items"]]
    assert keys == ["WEB-001"]
    assert dev.call("GET", f"/api/incidents/{secret['key']}")[0] == 404
    found = [i["key"] for i in dev.get("/api/search?q=perfil")["items"] if i.get("key")]
    assert secret["key"] not in found


def test_activity_visibility_permission(admin):
    dev = create_user(admin, "lola", "dev")
    new_incident(admin)
    mine = dev.get("/api/activity")
    assert not mine["all_users"]
    assert all(a["user_id"] == dev.get("/api/me")["user"]["id"] for a in mine["items"])
    everyone = admin.get("/api/activity")
    assert everyone["all_users"] and any("APP-001" in (a["entity_key"] or "") for a in everyone["items"])


def test_task_flow_with_dependency_and_verification(admin):
    dev = create_user(admin, "tito", "dev")
    tito = dev.get("/api/me")["user"]["id"]
    t1 = admin.post("/api/tasks", {"uid": uuid.uuid4().hex, "title": "Diseñar API", "assignee_id": tito})["item"]
    t2 = admin.post("/api/tasks", {"uid": uuid.uuid4().hex, "title": "Implementar API", "assignee_id": tito})["item"]
    admin.post("/api/dependencies", {"blocker_type": "task", "blocker": t1["key"], "blocked_type": "task", "blocked": t2["key"]})
    code, err = dev.call("PATCH", f"/api/tasks/{t2['id']}", {"status": "resuelto", "version": t2["version"]})
    assert code == 400 and err["error"] == "dependencias_abiertas"
    t1 = dev.get(f"/api/tasks/{t1['id']}")["item"]
    t1 = dev.patch(f"/api/tasks/{t1['id']}", {"status": "resuelto", "version": t1["version"]})["item"]
    assert t1["status"] == "en_verificacion"
    admin.post(f"/api/tasks/{t1['id']}/verify", {"accept": True})
    assert admin.get(f"/api/tasks/{t1['id']}")["item"]["status"] == "resuelto"
    assert "dependency_unblocked" in notif_types(dev)


def test_optimistic_concurrency(admin):
    inc = new_incident(admin)
    admin.patch(f"/api/incidents/{inc['key']}", {"title": "Primero", "version": inc["version"]})
    code, err = admin.call("PATCH", f"/api/incidents/{inc['key']}", {"title": "Segundo", "version": inc["version"]})
    assert code == 409 and err["error"] == "conflicto"


def test_idempotent_create(admin):
    body = {"uid": uuid.uuid4().hex, "title": "Duplicado por reintento", "priority": "baja", "category": "diseno"}
    a = admin.ok("POST", "/api/incidents", body)["item"]
    b = admin.ok("POST", "/api/incidents", body)["item"]
    assert a["id"] == b["id"]
    assert len(admin.get("/api/incidents")["items"]) == 1


def test_dashboard_lists(admin):
    dev = create_user(admin, "marta", "dev")
    inc = new_incident(admin, priority="critica")
    dev.post(f"/api/incidents/{inc['key']}/status", {"status": "en_progreso"})
    dev.post(f"/api/incidents/{inc['key']}/status", {"status": "resuelta", "solution": "Hecho"})
    d = admin.get("/api/dashboard")
    assert [x["key"] for x in d["to_verify"]] == [inc["key"]]
    assert dev.get("/api/dashboard")["waiting_verification"][0]["key"] == inc["key"]
