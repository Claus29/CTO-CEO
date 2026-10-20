# Actualizaciones obligatorias

## Reglas

- El programa consulta la última versión publicada en GitHub Releases al abrirse y cada pocas horas.
- **Anfitrión**: si hay una versión nueva, debe instalarla (ventana que no se puede cerrar con «Actualizar ahora»).
- **Miembros**: deben actualizarse en cuanto el anfitrión tiene una versión más nueva que la suya, o si el servidor
  exige una versión mínima (respuesta HTTP 426). Así nunca hay miembros por delante de un anfitrión sin actualizar.
- Una versión puede marcar una **versión mínima de seguridad**: por debajo de ella la actualización es obligatoria siempre.
- **Sin Internet nunca se bloquea el trabajo.** Si un miembro debe actualizarse y no puede descargarla, se le ofrece el
  enlace para hacerlo a mano.

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
