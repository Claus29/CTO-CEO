# Cambios

Todas las versiones siguen [versionado semántico](https://semver.org/lang/es/).

## [2.5.1] - 2026-10-29

- **Acceso por Internet: «Comprobar» dice exactamente qué falla.** Antes, si el túnel aparecía «Healthy» pero la
  «Public hostname» apuntaba mal (HTTPS en vez de HTTP, o el puerto 8080 del ejemplo), decía que no encontraba el
  túnel. Ahora distingue «el túnel no está conectado» (error 530/1033) de «el túnel está conectado pero no llega a
  DEV CONTROL» (error 502), indica el código de Cloudflare y la configuración exacta que hay que poner.

## [2.5.0] - 2026-10-29

- **Acceso por Internet sin VPN** (Ajustes → Acceso por Internet): con un túnel gratuito de Cloudflare y un dominio
  propio, los demás usan DEV CONTROL desde cualquier sitio escribiendo `https://devcontrol.tudominio.com` como
  servidor. El anfitrión sigue siendo tu PC y los datos no salen de él. Interruptor para activarlo o cortarlo al
  momento, botón «Comprobar» que dice exactamente qué falta, e instrucciones paso a paso ([docs/INTERNET.md](docs/INTERNET.md)).
- Seguridad por Internet: quien entra por el túnel nunca tiene los privilegios del equipo anfitrión, los intentos
  fallidos se bloquean por su IP real (sin bloquear al resto) y no se enseñan el nombre ni las IP internas del equipo.
- Los miembros aceptan direcciones `https://` y explican con claridad si el anfitrión o el túnel están apagados (sus
  cambios quedan en cola hasta que vuelva). Por Internet las llamadas no están disponibles y los adjuntos admiten
  hasta 95 MB (límite de Cloudflare).
- **Copia en la nube** (Copias de seguridad → Copia en la nube): cada copia se duplica en la carpeta que elijas
  (OneDrive, Google Drive, Dropbox o un disco externo), con botón «Copiar ahora». Si falla, la copia normal se hace
  igualmente y los administradores reciben un aviso.
- El equipo anfitrión ya no se suspende mientras DEV CONTROL está abierto (la pantalla sí se apaga), para que esté
  siempre disponible. Se puede desactivar con `mantener_despierto = no` en `config.ini`.

## [2.4.0] - 2026-10-24

- **Eliminar tareas por completo**: en la tarea, «Más» → «Eliminar la tarea…». Se borra con sus comentarios, adjuntos,
  tiempo registrado, dependencias, enlaces y avisos (pide confirmación; queda anotado en la actividad y la tarea sigue
  en las copias de seguridad anteriores). Puede hacerlo quien creó la tarea, el administrador y el CTO (permiso nuevo
  «Eliminar definitivamente cualquier tarea», que se puede dar a otros roles en Usuarios y roles).
- Quien tenía la tarea asignada o la seguía recibe un aviso de que se ha eliminado.

## [2.3.0] - 2026-10-23

- **Saber quién está en línea**: arriba, junto a «Conectado», aparecen las personas que elijas (por ejemplo, Julio) con
  un punto verde si están conectadas. Con «+» (o «Ver en línea») añades a quien quieras; al pulsar a una persona puedes
  llamarla, ver su ficha en Equipo o dejar de seguirla.
- **Aviso cuando alguien se conecta o se desconecta**: botón de la campana junto a cada persona en Equipo y en Usuarios
  y roles (y desde la barra de arriba). Llega como aviso de Windows aunque DEV CONTROL esté en la bandeja.
- Sin avisos falsos: un corte de red breve (menos de 30 segundos) no se avisa, y cuando el anfitrión se reinicia o se
  actualiza no llegan avisos de que «se ha conectado» todo el equipo.
- Se puede desactivar en Mi cuenta → Avisos («Cuando se conecta o desconecta alguien que sigues»).

## [2.2.1] - 2026-10-23

- **Arreglo importante de las actualizaciones**: al pulsar «Actualizar ahora» el programa se cerraba pero la versión
  nueva no llegaba a instalarse ni se volvía a abrir. Windows no dejaba arrancar el ayudante (PowerShell oculto) que la
  instalaba. Ahora la instala una copia del propio programa, que anota cada paso en `registro.txt`, comprueba la
  versión nueva y la abre (o vuelve a la anterior si algo falla). Tras actualizarse se abre igual que estaba (por
  ejemplo, oculto en la bandeja).
- Comprobado de principio a fin en un Windows real: una versión instalada se actualiza sola a la siguiente y vuelve a
  abrirse.

## [2.2.0] - 2026-10-23

- **Las actualizaciones se instalan solas.** Cuando se publica una versión nueva, el anfitrión la detecta (busca cada
  30 minutos), avisa 2 minutos antes («Actualizar ahora» o «Posponer 15 min») y se instala sola; después, el resto de
  equipos se actualiza desde el anfitrión de la misma forma. Nunca durante una llamada, siempre con copia previa y vuelta
  atrás automática si algo falla (y sin reintentar sola una versión que ha fallado).
- **Avisos de Windows de verdad**: salen aunque la ventana esté cerrada en la bandeja, se quedan en el Centro de
  actividades y al pulsarlos se abre lo que ha cambiado. Nunca se repiten ni te avisan de lo que haces tú.
- **Tareas**: aviso al crear una tarea en tus proyectos y en cada edición o cambio de estado (pendiente, en curso,
  bloqueada, en verificación, resuelta, reabierta), con quién lo hizo y qué cambió. Las ediciones seguidas se juntan.
- **Seguir** tareas, incidencias, páginas de la wiki o un proyecto entero para enterarte de todo.
- **Chat**: avisos con quién escribe y en qué canal, respuestas a tus mensajes, agrupados por canal y modo por canal
  (todos, solo menciones y respuestas, silenciado).
- **Tus pendientes** cada mañana y recordatorio de lo que lleva más de un día esperando tu verificación.
- Avisos nuevos en ideas, versiones (publicada o en riesgo), objetivos, builds y pruebas, wiki y decisiones, ausencias,
  calendario, cuenta (rol, proyectos, inicio de sesión desde un equipo nuevo) y servidor (copias, disco).
- **Mi cuenta → Avisos**: ajuste rápido (Todo / Lo importante / Solo lo mío), horario de silencio, hora del resumen,
  tipos de aviso por áreas y «Probar un aviso de Windows».
- «Buscar actualizaciones» en el icono de la bandeja ahora dice siempre el resultado.
- La **descripción de una tarea** (y la de incidencias e ideas) admite hasta **500 000 caracteres** (antes 20 000).

## [2.1.1] - 2026-10-21

- **Actualizaciones con el repositorio privado.** El anfitrión descarga las versiones de GitHub con una clave de acceso
  de solo lectura (Ajustes → Servidor y actualizaciones → Acceso a GitHub), guardada cifrada con su cuenta de Windows.
- Los demás equipos reciben la versión **del anfitrión** por la red privada, sin cuenta ni clave de GitHub. Siguen
  comprobando la firma y la huella del instalador antes de instalarlo.
- En cuanto el anfitrión tiene una versión más nueva, los miembros la piden al momento; el anfitrión descarga el
  instalador por adelantado para que esté listo.
- Mensajes claros si la clave falta, caduca o no tiene permiso.
- Llamadas: si Windows tiene reservados los puertos del repetidor (pasa con Hyper-V, WSL o Docker), se usa otro
  puerto libre en lugar de fallar.

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
