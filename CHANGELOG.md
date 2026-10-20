# Cambios

Todas las versiones siguen [versionado semántico](https://semver.org/lang/es/).

## [2.0.1] - 2026-10-20

- Instalador: recuperar datos anteriores ya es de verdad opcional. En la pantalla «¿Qué papel tiene este equipo?»
  hay una casilla «Tengo datos de la versión anterior y quiero recuperarlos»; sin marcarla no se pide nada.
  Antes el campo «opcional» no dejaba avanzar si se dejaba vacío.
- Se puede recuperar desde la carpeta del programa anterior, una base de datos `.db` o una copia `.zip`.
- Al abrir el programa se avisa de lo que se ha recuperado. Si la copia elegida no es válida, se abre la pantalla
  de recuperación en lugar de empezar sin datos. Nunca se importa encima de datos que ya existen.

## [2.0.0] - 2026-10-20

Primera versión pública de DEV CONTROL.

- Programa de Windows con instalador, ventana nativa (WebView2) e icono en la bandeja con estados de color.
- Anfitrión y miembros en red privada; elección del servidor al iniciar sesión y prueba de conexión.
- Actualizaciones obligatorias y firmadas, con copia previa, comprobación y vuelta atrás automática.
- Datos y copias fuera del programa (Documentos y AppData); modo recuperación si falta la base de datos.
- Importación de los datos de la versión 1.
- Verificación por quien creó la incidencia o tarea, con avisos en cada paso.
- Usuarios, roles y permisos configurables; proyectos con miembros; historial de actividad con permiso.
- Plantillas de reporte, detección de duplicados, SLA con escalado, dependencias y bloqueos.
- Builds y notas de versión, planes de prueba, publicaciones en tiendas, renovaciones y tareas recurrentes.
- Carga de trabajo, ausencias, daily asíncrono, registro de tiempo y «quién hace qué ahora».
- Chat por canales con menciones, recordatorios y conversión en tarea; wiki y registro de decisiones.
- Bóveda cifrada, integración con GitHub, informes y métricas, búsqueda global (Ctrl+K) y verificación en dos pasos.
