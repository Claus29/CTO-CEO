# Cambios

Todas las versiones siguen [versionado semántico](https://semver.org/lang/es/).

## [2.1.1] - 2026-10-21

- **Actualizaciones con el repositorio privado.** El anfitrión descarga las versiones de GitHub con una clave de acceso
  de solo lectura (Ajustes → Servidor y actualizaciones → Acceso a GitHub), guardada cifrada con su cuenta de Windows.
- Los demás equipos reciben la versión **del anfitrión** por la red privada, sin cuenta ni clave de GitHub. Siguen
  comprobando la firma y la huella del instalador antes de instalarlo.
- En cuanto el anfitrión tiene una versión más nueva, los miembros la piden al momento; el anfitrión descarga el
  instalador por adelantado para que esté listo.
- Mensajes claros si la clave falta, caduca o no tiene permiso.

## [2.1.0] - 2026-10-21

- **Llamadas de voz y vídeo entre cuentas**, de 2 personas o de grupo (hasta 8), con compartir pantalla. Se llama a
  la persona: suena en todos sus equipos con DEV CONTROL abierto y sigue en el que conteste.
- Funcionan desde cualquier red que llegue al anfitrión: si los equipos no pueden verse directamente, la llamada pasa
  sola por el repetidor integrado en el anfitrión (cifrada de extremo a extremo).
- Aviso de Windows y ventana delante al recibir una llamada, llamadas perdidas, «No molestar», historial y registro en
  los canales. Ocupado, llamadas cruzadas, cortes de red y cierres inesperados se resuelven sin llamadas fantasma.
- Permiso nuevo «Hacer y recibir llamadas» (activado para todos los roles salvo Observador).
- Aviso permanente de **versión obsoleta**: franja en el programa, aviso de Windows al abrir y cada 4 horas, e icono
  ámbar en la bandeja. La actualización obligatoria nunca interrumpe una llamada: se pide al colgar.
- El servidor exige la versión 2.1.0 a todos los equipos (para que todos tengan llamadas).
- «Permitir en el Firewall de Windows» abre también los puertos de las llamadas.

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
