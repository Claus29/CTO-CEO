# Datos y copias de seguridad

## Principio

Los datos **nunca** están en la carpeta del programa. Borrar, reinstalar o actualizar DEV CONTROL no los toca.

| Ubicación | Qué hay |
|---|---|
| `Documentos\DEV CONTROL\Copias de seguridad` | Copias `.zip` (base de datos + adjuntos + manifiesto con recuentos) |
| `Documentos\DEV CONTROL\Actualizaciones` | Instaladores descargados y `registro.txt` |
| `Documentos\DEV CONTROL\Exportaciones` | PDF y CSV exportados |
| `Documentos\DEV CONTROL\Configuración\config.ini` | Configuración del equipo (editable con el Bloc de notas) |
| `%LOCALAPPDATA%\DEV CONTROL\Servidor` | Base de datos en uso (`devcontrol.db`) y adjuntos del anfitrión |
| `%LOCALAPPDATA%\DEV CONTROL\Cliente` | Cola de cambios sin conexión y caché, por servidor |
| `%LOCALAPPDATA%\DEV CONTROL\Registros` | Registros de diagnóstico (sin contraseñas) |

La base de datos en uso no está en Documentos a propósito: OneDrive suele sincronizar esa carpeta y puede dañar una
base de datos abierta. Las copias sí están en Documentos para que OneDrive las respalde.

## Cuándo se hacen copias

- Automáticamente cada 24 h (configurable) y se conservan las últimas 30.
- Al detener el servidor (al salir del programa).
- Antes de cada actualización y antes de migrar la base de datos a un formato nuevo.
- Antes de restaurar otra copia (el estado actual nunca se pierde).
- A mano: **Ajustes → Copias de seguridad** o el menú del icono de la bandeja.

## Restaurar

**Ajustes → Copias de seguridad → Restaurar**. Antes se guarda el estado actual en otra copia y se aparta en
`Servidor\restauraciones`. Los números ya usados (`APP-015`) no se reutilizan nunca.

## Modo recuperación

Si al arrancar el anfitrión falta la base de datos pero existen copias, DEV CONTROL **no crea una base vacía**:
muestra la lista de copias para restaurar una (o una copia/base de datos de otra ubicación). Empezar sin datos exige
escribir `EMPEZAR VACIO`, y las copias no se borran.

## Cambiar de equipo anfitrión

1. En el anfitrión antiguo: **Copia de seguridad ahora** y copia el `.zip` al equipo nuevo, en
   `Documentos\DEV CONTROL\Copias de seguridad`.
2. Instala DEV CONTROL en el equipo nuevo como **Anfitrión**: al no tener base de datos y sí copias, entrará en modo
   recuperación; restaura la copia.
3. En los miembros, escribe la IP del nuevo anfitrión al iniciar sesión (o cámbiala en `config.ini`).
