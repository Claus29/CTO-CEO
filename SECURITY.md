# Seguridad

## Informar de una vulnerabilidad

No abras una incidencia pública. Usa **Security → Report a vulnerability** en este repositorio (aviso privado de
GitHub). Responderemos lo antes posible y publicaremos una versión corregida, marcada como versión mínima de seguridad
para que todos los equipos se actualicen.

## Diseño

- El servidor solo acepta conexiones desde redes privadas configuradas y nunca debe exponerse a Internet.
- Contraseñas con PBKDF2-SHA256 (600 000 iteraciones), bloqueo temporal por intentos fallidos, verificación en dos
  pasos (TOTP) opcional u obligatoria por rol, y sesiones revocables.
- Permisos comprobados en el servidor; cada usuario ve solo los proyectos de los que es miembro.
- Bóveda cifrada con AES-256-GCM y clave derivada con scrypt; la clave solo está en memoria mientras está desbloqueada.
- El programa local solo escucha en 127.0.0.1 y valida la cabecera `Host`.
- Actualizaciones firmadas con Ed25519 y comprobadas con SHA-256 antes de instalarse.
- Los registros no guardan contraseñas ni tokens.
