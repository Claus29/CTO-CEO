"""Calidad: builds, publicaciones en tiendas y planes de prueba."""
from __future__ import annotations

from . import auth
from .common import (
    BUILD_CHANNELS,
    PLATFORMS,
    STORE_STATUSES,
    STORES,
    TEST_RESULTS,
    check_version,
    clean_choice,
    clean_date,
    clean_int,
    clean_text,
    clean_uid,
    log_activity,
    names,
    notify,
    project_clause,
    require_project,
    users_with_perm,
)
from .db import now_iso
from .entities import find_entity
from .web import Response, bad_request, not_found


def register(router, app):
    db = app.db

    # -- builds ------------------------------------------------------------------------
    @router.route("GET", "/api/builds", perm="calidad.ver")
    def builds(req):
        pclause, params = project_clause(db, req.user, "b.project_id", req)
        where = [pclause]
        if req.q("version_id"):
            where.append("b.version_id = ?")
            params.append(req.q_int("version_id"))
        rows = db.query(f"SELECT b.*, v.name AS version_name, p.key AS project_key FROM builds b LEFT JOIN versions v "
                        f"ON v.id = b.version_id JOIN projects p ON p.id = b.project_id WHERE {' AND '.join(where)} "
                        f"ORDER BY b.id DESC", params)
        un = names(db.conn)
        for r in rows:
            r["platform_label"] = PLATFORMS.get(r["platform"])
            r["channel_label"] = BUILD_CHANNELS.get(r["channel"])
            r["created_by_name"] = un.get(r["created_by"])
            r["submissions"] = db.query("SELECT id, store, status FROM store_submissions WHERE build_id=?", (r["id"],))
            r["found_count"] = db.scalar("SELECT COUNT(*) FROM incidents WHERE found_build_id=?", (r["id"],))
            r["fixed_count"] = db.scalar("SELECT COUNT(*) FROM incidents WHERE fixed_build_id=?", (r["id"],))
        return {"items": rows}

    @router.route("POST", "/api/builds", perm="calidad.editar", idempotent=True)
    def new_build(req):
        data = req.json
        user = req.user
        project = require_project(db, user, data.get("project_id"))
        number = clean_text(data.get("number"), "número de build", 60, required=True)
        platform = clean_choice(data.get("platform"), "plataforma", PLATFORMS)
        with db.tx() as c:
            uid = clean_uid(data.get("uid"))
            dup = c.execute("SELECT id FROM builds WHERE uid=?", (uid,)).fetchone()
            if dup:
                return {"id": dup[0], "duplicate": True}
            if c.execute("SELECT 1 FROM builds WHERE project_id=? AND platform=? AND number=?",
                         (project["id"], platform, number)).fetchone():
                raise bad_request(f"Ya existe la build {number} para {PLATFORMS[platform]}.")
            vid = clean_int(data.get("version_id"), "versión", optional=True)
            if vid and not c.execute("SELECT 1 FROM versions WHERE id=? AND project_id=?", (vid, project["id"])).fetchone():
                raise bad_request("La versión no pertenece al proyecto.")
            cur = c.execute("INSERT INTO builds(uid, project_id, version_id, number, platform, channel, built_on, notes, "
                            "created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (uid, project["id"], vid, number, platform,
                             clean_choice(data.get("channel"), "canal", BUILD_CHANNELS, "interno"),
                             clean_date(data.get("built_on"), "fecha"), clean_text(data.get("notes"), "notas", 5000),
                             user["id"], now_iso()))
            log_activity(db, c, user, "build", cur.lastrowid, number, "created",
                         f"Registró la build {number} ({PLATFORMS[platform]})", project_id=project["id"])
            db.emit(c, "build", "build", cur.lastrowid, actor_id=user["id"])
            return Response.json({"id": cur.lastrowid}, 201)

    # -- publicaciones en tiendas ----------------------------------------------------------
    def ser_sub(r, un):
        r["store_label"] = STORES.get(r["store"])
        r["status_label"] = STORE_STATUSES.get(r["status"])
        r["created_by_name"] = un.get(r["created_by"])
        r["version"] = r["row_version"]
        if r.get("task_id"):
            t = db.query_one("SELECT key, status FROM tasks WHERE id=?", (r["task_id"],))
            r["task_key"] = t["key"] if t else None
        return r

    @router.route("GET", "/api/store-submissions", perm="calidad.ver")
    def submissions(req):
        pclause, params = project_clause(db, req.user, "b.project_id", req)
        rows = db.query(f"SELECT s.*, b.number AS build_number, b.platform, b.project_id, v.name AS version_name FROM "
                        f"store_submissions s JOIN builds b ON b.id = s.build_id LEFT JOIN versions v ON "
                        f"v.id = b.version_id WHERE {pclause} ORDER BY s.updated_at DESC", params)
        un = names(db.conn)
        return {"items": [ser_sub(r, un) for r in rows]}

    @router.route("POST", "/api/store-submissions", perm="calidad.editar", idempotent=True)
    def new_submission(req):
        data = req.json
        user = req.user
        with db.tx() as c:
            build = find_entity(db, user, "build", data.get("build_id") or "")
            uid = clean_uid(data.get("uid"))
            dup = c.execute("SELECT id FROM store_submissions WHERE uid=?", (uid,)).fetchone()
            if dup:
                return {"id": dup[0], "duplicate": True}
            store = clean_choice(data.get("store"), "tienda", STORES)
            ts = now_iso()
            cur = c.execute("INSERT INTO store_submissions(uid, build_id, store, status, notes, created_by, created_at, "
                            "updated_at) VALUES (?,?,?,?,?,?,?,?)",
                            (uid, build["id"], store, "preparando", clean_text(data.get("notes"), "notas", 5000),
                             user["id"], ts, ts))
            log_activity(db, c, user, "store", cur.lastrowid, build["number"], "created",
                         f"Preparó el envío de la build {build['number']} a {STORES[store]}",
                         project_id=build["project_id"])
            db.emit(c, "store", "store", cur.lastrowid, actor_id=user["id"])
            return Response.json({"id": cur.lastrowid}, 201)

    @router.route("PATCH", "/api/store-submissions/{sid}", perm="calidad.editar", idempotent=True)
    def edit_submission(req, sid):
        data = req.json
        user = req.user
        with db.tx() as c:
            row = c.execute("SELECT s.*, b.number, b.project_id, b.version_id FROM store_submissions s JOIN builds b "
                            "ON b.id = s.build_id WHERE s.id=?", (sid,)).fetchone()
            if not row:
                raise not_found("La publicación no existe.")
            row = dict(row)
            find_entity(db, user, "build", row["build_id"])
            check_version(row, data, "La publicación")
            sets, vals = [], []
            ts = now_iso()
            status = clean_choice(data.get("status"), "estado", STORE_STATUSES, row["status"])
            if status != row["status"]:
                sets.append("status=?")
                vals.append(status)
                stamp = {"enviada": "submitted_at", "aprobada": "decided_at", "rechazada": "decided_at",
                         "publicada": "released_at"}.get(status)
                if stamp:
                    sets.append(f"{stamp}=?")
                    vals.append(ts)
                summary = f"Build {row['number']} en {STORES[row['store']]}: {STORE_STATUSES[status]}"
                if status == "rechazada":
                    reason = clean_text(data.get("rejection_reason"), "motivo del rechazo", 5000, required=True)
                    sets.append("rejection_reason=?")
                    vals.append(reason)
                    # El rechazo crea una tarea de alta prioridad enlazada a la build.
                    # El uid solo admite letras, números, «-» y «_»: se quitan «:» y «.» de la marca de tiempo.
                    stamp_uid = ts.replace(":", "").replace(".", "")
                    tid = app.create_task(c, user, {
                        "uid": f"store-reject-{row['id']}-{stamp_uid}", "title": f"Corregir rechazo de {STORES[row['store']]} "
                                                                                f"(build {row['number']})",
                        "description": f"Motivo del rechazo:\n{reason}", "priority": "alta",
                        "version_id": row["version_id"]}, project_id=row["project_id"])
                    sets.append("task_id=?")
                    vals.append(tid)
                    summary += f" — {reason[:120]}"
                    notify(db, c, users_with_perm(c, "calidad.editar", row["project_id"]), "store",
                           f"Rechazo en {STORES[row['store']]}: build {row['number']}", reason[:200],
                           f"store-rejected-{row['id']}-{ts}", "store", row["id"], urgent=True, exclude=user["id"])
                elif status in ("aprobada", "publicada"):
                    notify(db, c, users_with_perm(c, "calidad.ver", row["project_id"]), "store", summary, "",
                           f"store-{status}-{row['id']}", "store", row["id"], exclude=user["id"])
                log_activity(db, c, user, "store", row["id"], row["number"], "status", summary,
                             project_id=row["project_id"])
            if "rollout_percent" in data:
                sets.append("rollout_percent=?")
                vals.append(clean_int(data["rollout_percent"], "porcentaje de despliegue", 0, 100, optional=True))
            if "notes" in data:
                sets.append("notes=?")
                vals.append(clean_text(data["notes"], "notas", 5000))
            if sets:
                c.execute(f"UPDATE store_submissions SET {', '.join(sets)}, updated_at=?, row_version=row_version+1 "
                          f"WHERE id=?", vals + [ts, row["id"]])
            db.emit(c, "store", "store", row["id"], actor_id=user["id"])
        return {"ok": True}

    # -- planes de prueba --------------------------------------------------------------------
    @router.route("GET", "/api/test-cases", perm="calidad.ver")
    def test_cases(req):
        pclause, params = project_clause(db, req.user, "project_id", req)
        rows = db.query(f"SELECT * FROM test_cases WHERE {pclause} AND archived=0 ORDER BY suite, id", params)
        return {"items": rows}

    @router.route("POST", "/api/test-cases", perm="calidad.editar", idempotent=True)
    def new_case(req):
        data = req.json
        user = req.user
        project = require_project(db, user, data.get("project_id"))
        with db.tx() as c:
            uid = clean_uid(data.get("uid"))
            dup = c.execute("SELECT id FROM test_cases WHERE uid=?", (uid,)).fetchone()
            if dup:
                return {"id": dup[0], "duplicate": True}
            ts = now_iso()
            cur = c.execute("INSERT INTO test_cases(uid, project_id, suite, title, steps, expected, created_by, created_at, "
                            "updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                            (uid, project["id"], clean_text(data.get("suite") or "General", "grupo", 80),
                             clean_text(data.get("title"), "título", 200, required=True),
                             clean_text(data.get("steps"), "pasos", 10000),
                             clean_text(data.get("expected"), "resultado esperado", 5000), user["id"], ts, ts))
            db.emit(c, "test", "test_case", cur.lastrowid, actor_id=user["id"])
            return Response.json({"id": cur.lastrowid}, 201)

    @router.route("PATCH", "/api/test-cases/{cid}", perm="calidad.editar", idempotent=True)
    def edit_case(req, cid):
        data = req.json
        with db.tx() as c:
            row = c.execute("SELECT * FROM test_cases WHERE id=?", (cid,)).fetchone()
            if not row:
                raise not_found("El caso de prueba no existe.")
            sets, vals = [], []
            for k, n in (("suite", 80), ("title", 200), ("steps", 10000), ("expected", 5000)):
                if k in data:
                    sets.append(f"{k}=?")
                    vals.append(clean_text(data[k], k, n, required=k == "title"))
            if "archived" in data:
                sets.append("archived=?")
                vals.append(1 if data["archived"] else 0)
            if sets:
                c.execute(f"UPDATE test_cases SET {', '.join(sets)}, updated_at=? WHERE id=?", vals + [now_iso(), row["id"]])
            db.emit(c, "test", "test_case", row["id"], actor_id=req.user["id"])
        return {"ok": True}

    def run_summary(run_id) -> dict:
        counts = {r["result"]: r["n"] for r in db.query("SELECT result, COUNT(*) n FROM test_results WHERE run_id=? "
                                                         "GROUP BY result", (run_id,))}
        total = sum(counts.values())
        executed = total - counts.get("pendiente", 0)
        return {"counts": counts, "total": total, "executed": executed,
                "pass_rate": round(100 * counts.get("ok", 0) / executed) if executed else None}

    @router.route("GET", "/api/test-runs", perm="calidad.ver")
    def test_runs(req):
        pclause, params = project_clause(db, req.user, "r.project_id", req)
        rows = db.query(f"SELECT r.*, b.number AS build_number, b.platform FROM test_runs r LEFT JOIN builds b "
                        f"ON b.id = r.build_id WHERE {pclause} ORDER BY r.id DESC LIMIT 200", params)
        un = names(db.conn)
        for r in rows:
            r["created_by_name"] = un.get(r["created_by"])
            r.update(run_summary(r["id"]))
        return {"items": rows}

    @router.route("POST", "/api/test-runs", perm="calidad.editar", idempotent=True)
    def new_run(req):
        data = req.json
        user = req.user
        project = require_project(db, user, data.get("project_id"))
        with db.tx() as c:
            uid = clean_uid(data.get("uid"))
            dup = c.execute("SELECT id FROM test_runs WHERE uid=?", (uid,)).fetchone()
            if dup:
                return {"id": dup[0], "duplicate": True}
            build_id = clean_int(data.get("build_id"), "build", optional=True)
            version_id = None
            if build_id:
                b = c.execute("SELECT version_id FROM builds WHERE id=? AND project_id=?", (build_id, project["id"])).fetchone()
                if not b:
                    raise bad_request("La build no pertenece al proyecto.")
                version_id = b[0]
            suite = (data.get("suite") or "").strip()
            cases = c.execute("SELECT id FROM test_cases WHERE project_id=? AND archived=0" +
                              (" AND suite=?" if suite else ""), (project["id"], suite) if suite else
                              (project["id"],)).fetchall()
            if not cases:
                raise bad_request("No hay casos de prueba para ejecutar en este proyecto" + (" y grupo." if suite else "."))
            cur = c.execute("INSERT INTO test_runs(uid, project_id, build_id, version_id, title, device, os_version, "
                            "created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                            (uid, project["id"], build_id, version_id,
                             clean_text(data.get("title") or "Ejecución de pruebas", "título", 200),
                             clean_text(data.get("device"), "dispositivo", 120),
                             clean_text(data.get("os_version"), "sistema", 120), user["id"], now_iso()))
            c.executemany("INSERT INTO test_results(run_id, case_id) VALUES (?, ?)", [(cur.lastrowid, x[0]) for x in cases])
            log_activity(db, c, user, "test_run", cur.lastrowid, None, "created",
                         f"Empezó una ejecución de pruebas ({len(cases)} casos)", project_id=project["id"])
            db.emit(c, "test", "test_run", cur.lastrowid, actor_id=user["id"])
            return Response.json({"id": cur.lastrowid}, 201)

    @router.route("GET", "/api/test-runs/{rid}", perm="calidad.ver")
    def run_detail(req, rid):
        run = db.query_one("SELECT r.*, b.number AS build_number, b.platform FROM test_runs r LEFT JOIN builds b "
                           "ON b.id = r.build_id WHERE r.id=?", (rid,))
        if not run:
            raise not_found("La ejecución no existe.")
        require_project(db, req.user, run["project_id"])
        results = db.query("SELECT tr.*, tc.suite, tc.title, tc.steps, tc.expected, i.key AS incident_key FROM "
                           "test_results tr JOIN test_cases tc ON tc.id = tr.case_id LEFT JOIN incidents i ON "
                           "i.id = tr.incident_id WHERE tr.run_id=? ORDER BY tc.suite, tc.id", (rid,))
        un = names(db.conn)
        for r in results:
            r["updated_by_name"] = un.get(r["updated_by"]) if r["updated_by"] else None
        run.update(run_summary(run["id"]))
        return {"run": run, "results": results}

    @router.route("POST", "/api/test-runs/{rid}/results/{cid}", perm="calidad.editar", idempotent=True)
    def set_result(req, rid, cid):
        data = req.json
        user = req.user
        result = clean_choice(data.get("result"), "resultado", TEST_RESULTS)
        with db.tx() as c:
            run = c.execute("SELECT * FROM test_runs WHERE id=?", (rid,)).fetchone()
            if not run:
                raise not_found("La ejecución no existe.")
            require_project(db, user, run["project_id"])
            row = c.execute("SELECT tr.*, tc.title, tc.steps, tc.expected FROM test_results tr JOIN test_cases tc ON "
                            "tc.id = tr.case_id WHERE tr.run_id=? AND tr.case_id=?", (rid, cid)).fetchone()
            if not row:
                raise not_found("El caso no forma parte de esta ejecución.")
            c.execute("UPDATE test_results SET result=?, note=?, updated_by=?, updated_at=? WHERE run_id=? AND case_id=?",
                      (result, clean_text(data.get("note"), "nota", 2000), user["id"], now_iso(), rid, cid))
            if c.execute("SELECT COUNT(*) FROM test_results WHERE run_id=? AND result='pendiente'", (rid,)).fetchone()[0] == 0:
                c.execute("UPDATE test_runs SET status='terminada', finished_at=COALESCE(finished_at, ?) WHERE id=?",
                          (now_iso(), rid))
            db.emit(c, "test", "test_run", int(rid), actor_id=user["id"])
        # Para un fallo, el cliente abre el formulario de incidencia con estos datos rellenados.
        prefill = None
        if result == "fallo" and not row["incident_id"]:
            prefill = {"title": f"Fallo en prueba: {row['title']}", "steps": row["steps"], "expected": row["expected"],
                       "found_build_id": run["build_id"], "device": run["device"], "os_version": run["os_version"],
                       "project_id": run["project_id"], "category": "errores", "test_run_id": int(rid),
                       "test_case_id": int(cid)}
        return {"ok": True, "incident_prefill": prefill}

    @router.route("POST", "/api/test-runs/{rid}/results/{cid}/incident", perm="calidad.editar", idempotent=True)
    def link_incident(req, rid, cid):
        with db.tx() as c:
            inc = find_entity(db, req.user, "incident", req.json.get("incident") or "")
            c.execute("UPDATE test_results SET incident_id=? WHERE run_id=? AND case_id=?", (inc["id"], rid, cid))
            db.emit(c, "test", "test_run", int(rid), actor_id=req.user["id"])
        return {"ok": True}

    _ = auth
