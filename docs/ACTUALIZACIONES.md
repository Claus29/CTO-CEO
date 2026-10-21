# Actualizaciones obligatorias

## Reglas

- El **anfitrión** consulta la última versión publicada en GitHub Releases al abrirse y cada pocas horas. Los
  **miembros** no necesitan GitHub: piden la versión al anfitrión por la red privada (ver «Repositorio privado»).
- **Anfitrión**: si hay una versión nueva, debe instalarla (ventana que no se puede cerrar con «Actualizar ahora»).
- **Miembros**: deben actualizarse en cuanto el anfitrión tiene una versión más nueva que la suya, o si el servidor
  exige una versión mínima (respuesta HTTP 426). Así nunca hay miembros por delante de un anfitrión sin actualizar.
- Una versión puede marcar una **versión mínima de seguridad**: por debajo de ella la actualización es obligatoria siempre.
- **Aviso permanente de versión obsoleta:** mientras exista una versión más nueva, cualquier equipo ve una franja
  «Tu versión de DEV CONTROL está obsoleta» con «Actualizar ahora» (se puede minimizar, pero vuelve al abrir), recibe
  un aviso de Windows al abrir y cada 4 horas, y el icono de la bandeja se pone ámbar.
- **Nunca se corta una llamada:** si la actualización pasa a ser obligatoria durante una llamada, la ventana
  obligatoria aparece al colgar.
- **Sin Internet nunca se bloquea el trabajo.** Si un miembro debe actualizarse y no puede descargarla, se le ofrece el
  enlace para hacerlo a mano.

## Repositorio privado

El repositorio puede ser privado. Entonces:

1. **Solo el anfitrión** necesita acceso a GitHub, con una **clave de acceso de solo lectura**:
   - En GitHub: foto de perfil → **Settings → Developer settings → Personal access tokens → Fine-grained tokens →
     Generate new token**.
   - *Repository access*: **Only select repositories** → el repositorio de DEV CONTROL.
   - *Permissions → Repository permissions → Contents*: **Read-only** (nada más).
   - Elige una caducidad (por ejemplo, 1 año) y copia la clave (`github_pat_…`).
   - En el anfitrión: **Ajustes → Servidor y actualizaciones → Acceso a GitHub**, pega la clave y pulsa
     **Guardar clave**. Se guarda cifrada con la cuenta de Windows de ese equipo (no se envía a nadie más).
2. El anfitrión descarga el manifiesto firmado y el instalador y **los sirve a los demás equipos**
   (`/api/update/manifest` y `/api/update/installer`). Los miembros siguen comprobando la firma y la huella SHA-256,
   así que el anfitrión no puede colarles un instalador distinto del publicado.
3. Si la clave caduca o se revoca, el anfitrión lo indica en Ajustes («GitHub ha rechazado la clave de acceso») y
   basta con poner una nueva.

Las versiones anteriores a la 2.1.1 solo saben descargar de un repositorio público: con el repositorio privado hay que
instalar la 2.1.1 a mano **una vez** en cada equipo (descargándola de Releases con una cuenta con acceso). A partir de
ahí todo se actualiza solo.

## Seguridad

Cada publicación incluye `manifest.json` (versión, notas, nombre, tamaño y SHA-256 del instalador) y
`manifest.json.sig`, su firma Ed25519. El programa lleva dentro la clave pública y **rechaza** cualquier manifiesto no
firmado o alterado y cualquier instalador cuya huella no coincida. La clave privada solo existe como secreto de GitHub
Actions (`DEVCONTROL_SIGNING_KEY`).

## Qué pasa al pulsar «Actualizar ahora»

1. Se descarga el instalador a `Documentos\DEV CONTROL\Actualizaciones` y se comprueba su huella.
2. En el anfitrión: se detiene el servidor, se hace una copia `previa-actualizacion` y se anotan los recuentos
   (usuarios, incidencias, tareas, mensajes…).
3. Se guarda una copia del programa actual y DEV CONTROL se cierra.
4. Se instala la versión nueva en modo silencioso (sin cambiar la configuración).
5. La versión nueva arranca en modo comprobación: abre la base de datos (aplicando migraciones si las hay), comprueba
   su integridad y que no falta ningún dato.
6. Si todo va bien, se abre normalmente y lo anota en `registro.txt`. Si algo falla, **vuelve sola a la versión
   anterior y a la copia de los datos** y también lo anota.

Ejemplo de `registro.txt`:

```
2026-10-20 19:30:02  Iniciando la actualización de 2.0.0 a 2.0.1.
2026-10-20 19:30:41  OK: actualizado de 2.0.0 a 2.0.1 correctamente. Datos comprobados: 152 incidencias, 87 tareas, 2310 mensajes, 6 usuarios.
```
