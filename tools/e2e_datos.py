"""Datos de prueba para las pruebas reales de actualización.

  python tools/e2e_datos.py crear       crea el administrador, 3 incidencias, 2 tareas y un mensaje
  python tools/e2e_datos.py comprobar   entra con el administrador y comprueba que siguen todos los datos

Habla con el programa local (http://127.0.0.1:8770), igual que la ventana de DEV CONTROL.
"""
from __future__ import annotations

import json
import sys
import urllib.request
import uuid

BASE = "http://127.0.0.1:8770"
USER, PASSWORD = "ana", "Zorro-azul-42"


def call(method, path, body=None, token=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    if method != "GET":
        req.add_header("Idempotency-Key", uuid.uuid4().hex)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"{}")


def login():
    return call("POST", "/api/login", {"username": USER, "password": PASSWORD})["token"]


def main(mode: str) -> int:
    if mode == "crear":
        call("POST", "/api/setup", {"admin": {"username": USER, "display_name": "Ana", "password": PASSWORD},
                                    "project_key": "APP", "project_name": "App"})
        tok = login()
        for i in range(3):
            call("POST", "/api/incidents", {"uid": uuid.uuid4().hex, "title": f"Incidencia de prueba {i}",
                                            "description": "Detalle", "priority": "alta", "category": "errores",
                                            "steps": "1. Abrir", "app_version": "1.0"}, tok)
        for i in range(2):
            call("POST", "/api/tasks", {"uid": uuid.uuid4().hex, "title": f"Tarea de prueba {i}"}, tok)
        call("POST", "/api/messages", {"uid": uuid.uuid4().hex, "body": "Hola equipo"}, tok)
    tok = login()
    incidents = len(call("GET", "/api/incidents", token=tok)["items"])
    tasks = len(call("GET", "/api/tasks", token=tok)["items"])
    version = call("GET", "/local/status")["version"]
    print(f"Versión {version}: {incidents} incidencias, {tasks} tareas")
    if incidents != 3 or tasks != 2:
        print("FALTAN DATOS")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "comprobar"))
