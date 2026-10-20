# DEV CONTROL

**Coordinación privada para equipos de desarrollo pequeños** (2–15 personas): incidencias con verificación,
tareas y tablero, planificación, calidad, chat, wiki y mucho más, en un programa de Windows que funciona
**dentro de tu red** (red local, VPN o Tailscale). Sin nube, sin cuotas y con tus datos siempre en tu equipo.

> *English:* DEV CONTROL is a private, self-hosted coordination app for small software teams (bug tracking with
> creator verification, tasks, planning, QA, chat and wiki). It runs as a native Windows program; one computer
> acts as the host and the rest connect over your private network. The interface is in Spanish.

---

## Qué resuelve

| Problema | Cómo lo resuelve DEV CONTROL |
|---|---|
| «Está arreglado» pero sigue fallando | Lo resuelto pasa a **En verificación** y solo cuenta como resuelto cuando **quien lo creó lo verifica** (o lo rechaza con motivo). |
| Nadie sabe en qué trabaja cada uno | **Historial de actividad** y vista «Ahora», con un permiso que decide quién puede verlo. |
| Reportes de errores incompletos | **Plantillas por categoría** con campos obligatorios y aviso de **posibles duplicados** mientras escribes. |
| Cosas bloqueadas que se olvidan | Bloqueos con motivo, **dependencias** que avisan cuando se desbloquean y **SLA** con escalado. |
| Perder datos al reinstalar | Los datos viven **fuera del programa** (Documentos y AppData), con copias automáticas y **modo recuperación**. |
| Versiones distintas en cada equipo | **Actualizaciones obligatorias y firmadas** desde el propio programa, con vuelta atrás automática. |

## Funcionalidades

- **Incidencias** con prefijo por proyecto (`INC-001`, `APP-014`…), prioridad, categoría, responsable, SLA, adjuntos,
  comentarios con @menciones, historial de resoluciones, reaperturas y duplicados.
- **Verificación obligatoria** de incidencias y tareas por quien las creó, con avisos en cada paso.
- **Tareas** en lista o **tablero kanban** (límites WIP), checklist, estimación, recurrencia y dependencias.
- **Proyectos** con miembros: cada persona ve solo sus proyectos (salvo permiso).
- **Usuarios, roles y permisos** configurables (Administrador, CTO, Desarrollo, QA, Observador o los tuyos).
- **Planificación**: ideas con votos, plan semanal, versiones con notas generadas, **roadmap con previsión**,
  calendario y **renovaciones** (certificados, dominios, cuentas de desarrollador…).
- **Calidad**: builds, planes y ejecuciones de prueba (un fallo se convierte en incidencia) y publicaciones en tiendas.
- **Equipo**: carga de trabajo frente a capacidad, ausencias, **daily asíncrono**, registro de tiempo y «quién hace qué ahora».
- **Chat** por canales con respuestas, menciones, adjuntos, recordatorios y «convertir en tarea».
- **Conocimiento**: wiki con historial y **registro de decisiones** (`DEC-001`).
- **Bóveda** cifrada para secretos del equipo (AES-GCM + scrypt), con registro de accesos.
- **Integración con GitHub**: los commits y PR que citan `APP-012` se enlazan; una PR fusionada deja la incidencia
  pendiente de verificación.
- **Informes y métricas** (primera respuesta, lead time, rechazos, cumplimiento de SLA…), exportación a PDF y CSV.
- **Búsqueda global** con `Ctrl+K`, **verificación en dos pasos** (TOTP), sesiones por equipo y modo claro/oscuro.
- **Funciona sin conexión**: lo que haces sin red se guarda y se envía solo al volver, sin duplicados.

## Cómo funciona

```
 Equipo ANFITRIÓN                         Equipos MIEMBROS
 ┌──────────────────────────────┐         ┌─────────────────────────┐
 │ DEV CONTROL                  │  red    │ DEV CONTROL             │
 │  · servidor del equipo  ◄────┼─privada─┼─ programa local         │
 │  · base de datos (AppData)   │ (LAN,   │  · cola sin conexión    │
 │  · copias (Documentos)       │  VPN,   │  · ventana + bandeja    │
 │  · ventana + bandeja         │ Tailsc.)│                         │
 └──────────────────────────────┘         └─────────────────────────┘
```

- El servidor **solo acepta conexiones de redes privadas** y nunca se expone a Internet.
- Al iniciar sesión puedes **elegir el servidor** (vacío = el de por defecto de tu equipo).
- El icono de la **bandeja del sistema** muestra el estado (verde, ámbar, rojo) y permite iniciar, detener o cerrar
  el servidor, hacer una copia o buscar actualizaciones.

## Instalación

1. Descarga `DEVCONTROL-Setup-<versión>.exe` desde [Releases](https://github.com/Claus29/CTO-CEO/releases/latest).
2. Ejecútalo (no necesita permisos de administrador). Si Windows SmartScreen avisa de que el editor es desconocido,
   pulsa **Más información → Ejecutar de todas formas** (el instalador no está firmado con un certificado de pago;
   las actualizaciones sí van firmadas por el proyecto).
3. Elige **Anfitrión** en el equipo que guardará los datos (déjalo encendido) o **Miembro** en el resto, escribiendo
   la IP del anfitrión y pulsando **Probar conexión**.
4. En el anfitrión, la primera vez se crea el **administrador** y el primer proyecto. Después invita al equipo desde
   **Usuarios y roles**.

Guía completa: [docs/INSTALACION.md](docs/INSTALACION.md).

## Dónde están tus datos

| Carpeta | Contenido |
|---|---|
| `Documentos\DEV CONTROL\Copias de seguridad` | Copias completas (automáticas, al cerrar, antes de actualizar y manuales) |
| `Documentos\DEV CONTROL\Actualizaciones` | Instaladores descargados y `registro.txt` con el resultado de cada actualización |
| `Documentos\DEV CONTROL\Exportaciones` | Informes PDF y CSV |
| `Documentos\DEV CONTROL\Configuración\config.ini` | Configuración de este equipo |
| `%LOCALAPPDATA%\DEV CONTROL\Servidor` | Base de datos en uso y adjuntos (anfitrión) |

Desinstalar o reinstalar **nunca** borra estas carpetas. Más en [docs/DATOS_Y_COPIAS.md](docs/DATOS_Y_COPIAS.md).

## Actualizaciones

DEV CONTROL busca versiones nuevas en este repositorio. Si hay una, **la actualización es obligatoria**: el anfitrión
se actualiza primero y, en cuanto lo hace, los demás equipos deben actualizarse para seguir trabajando. Cada versión
está **firmada** (Ed25519) y se comprueba su huella antes de instalarla; antes se hace una copia de seguridad y,
después, se verifica que la versión nueva arranca y que no falta ningún dato. Si algo falla, se vuelve solo a la
versión anterior. Sin Internet, nunca se bloquea el trabajo. Detalles: [docs/ACTUALIZACIONES.md](docs/ACTUALIZACIONES.md).

## Desarrollo

```bash
python -m pip install -r requirements-dev.txt
python -m pytest                 # pruebas
python -m ruff check .           # estilo
python -m devcontrol --sin-ventana   # servidor + interfaz en http://127.0.0.1:8770
```

En Windows, `pip install -r requirements-windows.txt` y `python tools/build_windows.py` generan el instalador
(necesita [Inno Setup 6](https://jrsoftware.org/isdl.php)). Arquitectura: [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md).
Publicar una versión: [docs/PUBLICAR_VERSION.md](docs/PUBLICAR_VERSION.md). Cómo contribuir: [CONTRIBUTING.md](CONTRIBUTING.md).

## Licencia

[MIT](LICENSE).
