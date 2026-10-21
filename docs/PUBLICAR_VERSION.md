# Publicar una versión

## Preparación (una sola vez)

1. En GitHub: **Settings → Secrets and variables → Actions → New repository secret**.
2. Nombre `DEVCONTROL_SIGNING_KEY`, valor: la clave privada de firma (texto base64 de 44 caracteres).
   Guárdala también en un gestor de contraseñas: **si se pierde, los programas instalados no aceptarán nuevas
   versiones** firmadas con otra clave. Nunca la subas al repositorio.

## Cada versión

1. Cambia `APP_VERSION` en `devcontrol/__init__.py` (y `MIN_CLIENT_VERSION` si los programas antiguos ya no deben
   poder conectarse).
2. Añade la sección de la versión en `CHANGELOG.md` (es el texto de «Novedades» que verá el equipo).
3. Si la base de datos cambia, sube `SCHEMA_VERSION` y añade la migración en `server/migrations.py` con su prueba.
4. Haz commit y súbelo. Después, una de dos:
   - En GitHub: **Actions → Publicar versión → Run workflow** (crea la etiqueta `v<versión>` sola), o
   - crea la etiqueta y súbela:
     ```bash
     git tag v2.1.1
     git push origin v2.1.1
     ```
5. La acción **Publicar versión** ejecuta las pruebas, compila el instalador, firma el manifiesto y crea la
   publicación con `DEVCONTROL-Setup-2.1.1.exe`, `manifest.json` y `manifest.json.sig`. Si falta el secreto de firma,
   falla con un aviso claro y no publica nada.

En pocas horas (o al pulsar «Buscar actualizaciones») el anfitrión se actualizará y, tras él, el resto del equipo.
Si el repositorio es privado, el anfitrión necesita su clave de acceso de solo lectura
([ACTUALIZACIONES.md](ACTUALIZACIONES.md#repositorio-privado)); los demás equipos la reciben del anfitrión.

## Dar acceso a otra persona (repositorio privado)

**Settings → Collaborators → Add people** y escribe su usuario de GitHub. Solo hace falta para quien vaya a ver el
código o descargar instaladores a mano: para usar DEV CONTROL y actualizarse no se necesita cuenta de GitHub.
