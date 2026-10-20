# Arquitectura

```
devcontrol/
  __main__.py      punto de entrada (ventana, --inicio, --sin-ventana, --copia, --diagnostico…)
  desktop.py       una sola instancia, servidor del anfitrión, ventana WebView2 (pywebview) e icono en la bandeja (pystray)
  updater.py       actualizaciones firmadas, aplicación, comprobación y vuelta atrás
  config.py        config.ini (con comentarios) y valores del instalador
  paths.py         carpetas de datos (Documentos y AppData); DEVCONTROL_HOME para pruebas
  agent/main.py    programa local en 127.0.0.1: sirve la interfaz, proxy al servidor elegido, cola sin conexión y caché por servidor
  server/          servidor del equipo (solo biblioteca estándar + cryptography)
    web.py         mini framework HTTP (ThreadingHTTPServer) con rutas, permisos e idempotencia
    db.py          SQLite en modo WAL, escrituras serializadas, cambios para el tiempo real, FTS5
    migrations.py  esquema numerado; conversión 1.x → 2.0 en una única transacción con copia previa
    auth.py        contraseñas PBKDF2, sesiones, TOTP, códigos de recuperación, permisos por rol
    main.py        arranque, configuración inicial, sesión, panel, actividad, modo recuperación, tareas programadas
    incidents.py, planning.py, relations.py, quality.py, chat.py, team.py, knowledge.py, reports.py,
    integrations.py, notifications.py, backups.py, exports.py, attachments.py, pdf.py
  web/             interfaz (HTML + CSS + JavaScript en módulos, sin dependencias)
```

## Decisiones

- **Sin dependencias pesadas en el servidor**: biblioteca estándar de Python y `cryptography`. Menos que actualizar y
  nada que se rompa con el tiempo.
- **Un único bloqueo de escritura** en SQLite: para equipos pequeños es lo más fiable; ningún identificador se repite y
  un corte de luz no deja nada a medias.
- **Tiempo real por long polling** (`/api/changes`) con época: tras restaurar una copia los clientes recargan.
- **Concurrencia optimista**: cada edición lleva la versión del elemento; si otra persona lo cambió, 409 y se recarga.
- **Idempotencia**: cada alta lleva un `uid` y cada envío una `Idempotency-Key`; reenviar desde la cola nunca duplica.
- **Permisos en el servidor**: la interfaz solo oculta lo que no se puede hacer.
- **Proyectos visibles**: toda consulta se filtra por los proyectos del usuario (salvo el permiso de verlos todos).
- **Red**: el servidor escucha en todas las interfaces pero rechaza cualquier IP fuera de los rangos privados
  configurados (`redes_permitidas`) y, opcionalmente, fuera de una lista de equipos.
- **Programa local**: comprueba la cabecera `Host` (protección frente a DNS rebinding) y envía su versión al servidor
  (`X-DevControl-Version`) para que este pueda exigir una mínima (HTTP 426).

## Pruebas

`python -m pytest` levanta servidores reales en puertos libres con datos temporales: flujos de verificación, permisos,
proyectos, sin conexión, copias, recuperación, migración desde una base real de la versión 1 y actualizaciones firmadas.
La CI de GitHub las ejecuta en Linux y Windows y compila el instalador.
