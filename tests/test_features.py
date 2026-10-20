"""Funcionalidades: ideas y votos, chat con menciones, bóveda, wiki, decisiones, calidad, tiempo y exportaciones."""
import uuid

from conftest import create_user, new_incident


def uid():
    return uuid.uuid4().hex


def test_ideas_votes_and_conversion(admin):
    dev = create_user(admin, "ivan", "dev")
    idea = admin.post("/api/ideas", {"uid": uid(), "title": "Modo oscuro"})
    iid = (idea.get("item") or idea)["id"]
    dev.post(f"/api/ideas/{iid}/vote", {})
    items = admin.get("/api/ideas")["items"]
    assert items[0]["votes"] == 2  # quien la propone también la vota
    task = admin.post(f"/api/ideas/{iid}/to-task", {"uid": uid()})
    assert task["task_key"].startswith("T-")


def test_chat_mentions_and_read_receipts(admin):
    dev = create_user(admin, "nuria", "dev")
    sent = admin.post("/api/messages", {"uid": uid(), "body": "@nuria revisa APP-001 cuando puedas"})
    assert "mention" in [n["type"] for n in dev.get("/api/notifications")["items"]]
    ch = dev.get("/api/channels")["items"]
    assert sum(c["unread"] for c in ch) == 1
    mid = (sent.get("item") or sent)["id"]
    dev.post("/api/messages/read", {"channel_id": ch[0]["id"], "last_id": mid})
    assert sum(c["unread"] for c in dev.get("/api/channels")["items"]) == 0


def test_vault_secrets(admin):
    admin.post("/api/vault/setup", {"passphrase": "Maestra-muy-larga-1"})
    item = admin.post("/api/vault/items", {"uid": uid(), "name": "Consola de Google Play", "username": "equipo",
                                           "secret": "s3cr3t0-de-prueba"})
    vid = (item.get("item") or item)["id"]
    listed = admin.get("/api/vault/items")["items"]
    assert "s3cr3t0" not in str(listed)               # el listado nunca muestra secretos
    assert admin.post(f"/api/vault/items/{vid}/reveal", {})["secret"] == "s3cr3t0-de-prueba"
    admin.post("/api/vault/lock", {})
    assert admin.call("POST", f"/api/vault/items/{vid}/reveal", {})[0] >= 400
    code, _ = admin.call("POST", "/api/vault/unlock", {"passphrase": "incorrecta-del-todo"})
    assert code >= 400
    admin.post("/api/vault/unlock", {"passphrase": "Maestra-muy-larga-1"})
    assert admin.post(f"/api/vault/items/{vid}/reveal", {})["secret"] == "s3cr3t0-de-prueba"
    assert admin.get("/api/vault/log")["items"]
    dev = create_user(admin, "vero", "dev")
    assert dev.call("GET", "/api/vault/items")[0] == 403


def test_wiki_and_decisions(admin):
    page = admin.post("/api/wiki", {"uid": uid(), "title": "Cómo publicar", "body": "Paso 1"})
    pid = (page.get("item") or page)["id"]
    current = admin.get(f"/api/wiki/{pid}")
    cur = current.get("item") or current
    admin.patch(f"/api/wiki/{pid}", {"body": "Paso 1\nPaso 2", "version": cur["version"]})
    again = admin.get(f"/api/wiki/{pid}")
    assert len(again.get("revisions") or (again.get("item") or {}).get("revisions") or []) >= 1
    dec = admin.post("/api/decisions", {"uid": uid(), "title": "Usar SQLite", "context": "Equipo pequeño",
                                        "decision": "SQLite con WAL"})
    key = (dec.get("item") or dec)["key"]
    assert key.startswith("DEC-")
    assert any(i["key"] == key for i in admin.get("/api/search?q=SQLite")["items"])


def test_quality_builds_and_test_runs(admin):
    pid = admin.get("/api/me")["default_project_id"]
    build = admin.post("/api/builds", {"uid": uid(), "project_id": pid, "number": "1.0 (42)", "platform": "android", "channel": "beta"})
    bid = (build.get("item") or build)["id"]
    case = admin.post("/api/test-cases", {"uid": uid(), "project_id": pid, "title": "Iniciar sesión", "steps": "Abrir", "expected": "Entra"})
    cid = (case.get("item") or case)["id"]
    run = admin.post("/api/test-runs", {"uid": uid(), "project_id": pid, "build_id": bid, "title": "Regresión"})
    rid = (run.get("item") or run)["id"]
    admin.post(f"/api/test-runs/{rid}/results/{cid}", {"result": "fallo", "note": "No entra"})
    inc = new_incident(admin, title="No se puede iniciar sesión")
    admin.post(f"/api/test-runs/{rid}/results/{cid}/incident", {"incident": inc["key"]})
    detail = admin.get(f"/api/test-runs/{rid}")
    assert inc["id"] in [r.get("incident_id") for r in detail.get("results", [])] or inc["key"] in str(detail)
    sub = admin.post("/api/store-submissions", {"uid": uid(), "build_id": bid, "store": "google_play"})
    assert (sub.get("item") or sub)["id"]


def test_time_tracking(admin):
    inc = new_incident(admin)
    admin.post("/api/time/start", {"entity_type": "incident", "entity": inc["key"]})
    assert admin.get("/api/time/current")["entry"]["key"] == inc["key"]
    admin.post("/api/time/stop", {})
    assert admin.get("/api/time/current")["entry"] is None
    assert admin.get("/api/time")["items"]


def test_exports(admin):
    new_incident(admin)
    code, csv = admin.call("GET", "/api/export/incidents.csv")
    assert code == 200 and b"APP-001" in csv
    code, pdf = admin.call("GET", "/api/export/report.pdf")
    assert code == 200 and pdf.startswith(b"%PDF")
    assert b"DEV CONTROL" in pdf


def test_reports_and_workload(admin):
    new_incident(admin)
    rep = admin.get("/api/reports")
    assert rep["incidents"]["created"] == 1 and len(rep["weekly"]) == 12
    assert "people" in admin.get("/api/workload") or admin.get("/api/workload")


def test_github_integration_without_network(admin, server, monkeypatch):
    import devcontrol.server.integrations as gh
    inc = new_incident(admin)
    admin.ok("PUT", "/api/integrations/github", {"repo": "equipo/app", "enabled": True})

    real = gh.urllib.request.urlopen

    def fake_urlopen(req, timeout=20):
        import io
        import json
        url = req.full_url
        if "api.github.com" not in url:
            return real(req, timeout=timeout)
        data = [] if "/commits" in url else [{"number": 7, "title": f"Arregla {inc['key']}", "body": "",
                                                "merged_at": "2026-01-01T00:00:00Z", "state": "closed",
                                                "html_url": "https://github.com/equipo/app/pull/7",
                                                "user": {"login": "dev"}, "head": {"ref": "fix"}}]

        class R(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return R(json.dumps(data).encode())

    monkeypatch.setattr(gh.urllib.request, "urlopen", fake_urlopen)
    result = admin.post("/api/integrations/github/poll", {})
    assert result["transitions"] == 1
    # Una PR fusionada nunca cierra sola: deja la incidencia pendiente de verificación
    assert admin.get(f"/api/incidents/{inc['key']}")["item"]["status"] == "en_verificacion"
