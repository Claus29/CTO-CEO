"""Catálogo de permisos y roles predefinidos.

Los permisos se comprueban SIEMPRE en el servidor. La interfaz solo oculta lo
que el usuario no puede hacer.
"""
from __future__ import annotations

# (clave, área, descripción)
CATALOG: list[tuple[str, str, str]] = [
    ("incidencias.ver", "Incidencias", "Ver incidencias"),
    ("incidencias.crear", "Incidencias", "Crear incidencias formales"),
    ("incidencias.editar", "Incidencias", "Editar los campos de una incidencia (título, prioridad…)"),
    ("incidencias.trabajar", "Incidencias", "Trabajar en incidencias: en progreso, bloquear, volver a pendiente"),
    ("incidencias.resolver", "Incidencias", "Marcar incidencias como resueltas (pasan a verificación)"),
    ("incidencias.verificar_todas", "Incidencias", "Verificar o rechazar cualquier incidencia, no solo las creadas"),
    ("incidencias.reabrir", "Incidencias", "Reabrir incidencias resueltas"),
    ("incidencias.comentar", "Incidencias", "Comentar en incidencias y tareas"),
    ("incidencias.adjuntar", "Incidencias", "Adjuntar archivos"),
    ("incidencias.asignar", "Incidencias", "Asignar incidencias a otras personas"),
    ("tareas.ver", "Tareas", "Ver tareas"),
    ("tareas.crear", "Tareas", "Crear tareas"),
    ("tareas.editar", "Tareas", "Editar tareas de otros"),
    ("tareas.trabajar", "Tareas", "Trabajar en tareas y moverlas en el tablero"),
    ("tareas.verificar_todas", "Tareas", "Verificar o rechazar cualquier tarea, no solo las creadas"),
    ("tareas.asignar", "Tareas", "Asignar tareas a otras personas"),
    ("tareas.eliminar", "Tareas", "Eliminar definitivamente cualquier tarea (cada uno puede eliminar las que creó)"),
    ("planificacion.ver", "Planificación", "Ver ideas, plan semanal, versiones, roadmap, calendario y renovaciones"),
    ("planificacion.editar", "Planificación", "Editar la planificación"),
    ("calidad.ver", "Calidad", "Ver builds, planes de prueba y publicaciones en tiendas"),
    ("calidad.editar", "Calidad", "Gestionar builds, planes de prueba y publicaciones en tiendas"),
    ("chat.usar", "Chat", "Usar el chat"),
    ("chat.canales", "Chat", "Crear y archivar canales"),
    ("llamadas.usar", "Chat", "Hacer y recibir llamadas de voz y vídeo"),
    ("actividad.ver_propia", "Actividad", "Ver la propia actividad"),
    ("actividad.ver_todos", "Actividad", "Ver la actividad de todo el equipo y en qué trabaja cada uno"),
    ("conocimiento.ver", "Conocimiento", "Ver la wiki y el registro de decisiones"),
    ("conocimiento.editar", "Conocimiento", "Editar la wiki y las decisiones"),
    ("boveda.ver", "Bóveda", "Ver los secretos permitidos para su rol"),
    ("boveda.gestionar", "Bóveda", "Gestionar la bóveda (crear, editar, permisos, desbloquear)"),
    ("informes.ver", "Informes", "Ver métricas e informes del equipo"),
    ("exportar", "Informes", "Exportar a PDF y CSV"),
    ("equipo.ver", "Equipo", "Ver la carga de trabajo, ausencias y daily del equipo"),
    ("proyectos.ver_todos", "Proyectos", "Ver todos los proyectos aunque no sea miembro"),
    ("proyectos.gestionar", "Proyectos", "Crear proyectos y gestionar sus miembros"),
    ("sistema.copias", "Sistema", "Copias de seguridad y restauración"),
    ("sistema.ajustes", "Sistema", "Ajustes del sistema (SLA, plantillas, avisos…)"),
    ("sistema.usuarios", "Sistema", "Gestionar usuarios y roles"),
    ("sistema.integraciones", "Sistema", "Configurar integraciones (GitHub)"),
]
ALL_PERMISSIONS = [p[0] for p in CATALOG]

_READ = ["incidencias.ver", "tareas.ver", "planificacion.ver", "calidad.ver", "conocimiento.ver",
         "actividad.ver_propia", "chat.usar"]
# Permisos añadidos en versiones posteriores: se conceden una sola vez a estos roles en las bases de datos existentes.
ADDED_PERMISSIONS = {"llamadas.usar": ["cto", "dev", "qa"], "tareas.eliminar": ["cto"]}

DEFAULT_ROLES = [
    {
        "key": "admin", "name": "Administrador / CEO", "locked": True,
        "description": "Acceso total. Siempre debe existir al menos un administrador activo.",
        "perms": ALL_PERMISSIONS,
    },
    {
        "key": "cto", "name": "Responsable técnico (CTO)", "locked": False,
        "description": "Trabaja y verifica incidencias y tareas, gestiona la planificación y ve la actividad de todos.",
        "perms": _READ + [
            "incidencias.trabajar", "incidencias.resolver", "incidencias.verificar_todas", "incidencias.reabrir",
            "incidencias.comentar", "incidencias.adjuntar", "incidencias.asignar",
            "tareas.crear", "tareas.editar", "tareas.trabajar", "tareas.verificar_todas", "tareas.asignar",
            "tareas.eliminar", "planificacion.editar", "calidad.editar", "chat.canales", "actividad.ver_todos",
            "conocimiento.editar", "boveda.ver", "informes.ver", "exportar", "equipo.ver", "proyectos.ver_todos",
            "llamadas.usar",
        ],
    },
    {
        "key": "dev", "name": "Desarrollador", "locked": False,
        "description": "Trabaja en lo asignado, comenta, usa el chat y crea tareas.",
        "perms": _READ + [
            "incidencias.trabajar", "incidencias.resolver", "incidencias.comentar", "incidencias.adjuntar",
            "tareas.crear", "tareas.trabajar", "conocimiento.editar", "llamadas.usar",
        ],
    },
    {
        "key": "qa", "name": "QA / Tester", "locked": False,
        "description": "Crea incidencias, ejecuta planes de prueba y verifica resoluciones.",
        "perms": _READ + [
            "incidencias.crear", "incidencias.comentar", "incidencias.adjuntar", "incidencias.verificar_todas",
            "incidencias.reabrir", "tareas.crear", "calidad.editar", "conocimiento.editar", "llamadas.usar",
        ],
    },
    {
        "key": "observer", "name": "Observador", "locked": False,
        "description": "Solo lectura.",
        "perms": _READ,
    },
]

DEFAULT_ONBOARDING = {
    "admin": ["Crear el primer proyecto", "Invitar al equipo (Usuarios y roles)", "Configurar copias y avisos",
              "Revisar los SLA por prioridad"],
    "cto": ["Leer la guía de uso del equipo", "Revisar las incidencias abiertas", "Configurar tus avisos",
            "Activar la verificación en dos pasos"],
    "dev": ["Leer la guía de uso del equipo", "Configurar tus avisos", "Abrir tu primera tarea asignada",
            "Rellenar tu primer daily", "Activar la verificación en dos pasos"],
    "qa": ["Leer la guía de uso del equipo", "Revisar los planes de prueba", "Configurar tus avisos"],
    "observer": ["Leer la guía de uso del equipo"],
}


def seed_roles(conn, now: str) -> dict[str, int]:
    ids = {}
    for role in DEFAULT_ROLES:
        row = conn.execute("SELECT id FROM roles WHERE key=?", (role["key"],)).fetchone()
        if row:
            ids[role["key"]] = row[0]
            continue
        cur = conn.execute("INSERT INTO roles(key, name, description, locked, created_at, updated_at) "
                           "VALUES (?,?,?,?,?,?)", (role["key"], role["name"], role["description"],
                                                    1 if role["locked"] else 0, now, now))
        ids[role["key"]] = cur.lastrowid
        conn.executemany("INSERT OR IGNORE INTO role_permissions(role_id, perm) VALUES (?, ?)",
                         [(cur.lastrowid, p) for p in role["perms"]])
        import json
        conn.execute("INSERT OR IGNORE INTO onboarding_templates(role_id, items) VALUES (?, ?)",
                     (cur.lastrowid, json.dumps(DEFAULT_ONBOARDING.get(role["key"], []), ensure_ascii=False)))
    # Permisos nuevos de versiones posteriores (una sola vez, para respetar los cambios del administrador)
    row = conn.execute("SELECT value FROM meta WHERE key='permisos_anadidos'").fetchone()
    import json as _json
    done = set(_json.loads(row[0])) if row else set()
    for perm, role_keys in ADDED_PERMISSIONS.items():
        if perm in done:
            continue
        for rk in role_keys:
            rid = conn.execute("SELECT id FROM roles WHERE key=?", (rk,)).fetchone()
            if rid:
                conn.execute("INSERT OR IGNORE INTO role_permissions(role_id, perm) VALUES (?, ?)", (rid[0], perm))
        done.add(perm)
    conn.execute("INSERT INTO meta(key, value) VALUES ('permisos_anadidos', ?) ON CONFLICT(key) DO UPDATE SET "
                 "value=excluded.value", (_json.dumps(sorted(done)),))
    # El rol de administrador siempre tiene todos los permisos (también los nuevos de futuras versiones).
    admin_id = ids.get("admin")
    if admin_id:
        conn.executemany("INSERT OR IGNORE INTO role_permissions(role_id, perm) VALUES (?, ?)",
                         [(admin_id, p) for p in ALL_PERMISSIONS])
    return ids
