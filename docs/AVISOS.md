# Avisos

DEV CONTROL avisa a cada persona de lo que le afecta, dentro del programa (campana «Avisos») y con **avisos de
Windows** que salen aunque la ventana esté cerrada en la bandeja. Al pulsar un aviso de Windows se abre justo lo que
ha cambiado.

## Quién se entera de qué

| Qué pasa | Quién recibe el aviso |
|---|---|
| Tarea **creada** | Miembros del proyecto (salvo quien elija «Solo las mías») y quien siga el proyecto |
| Tarea o incidencia **editada** (título, prioridad, fecha, checklist…) | Quien la creó, su responsable, sus seguidores y quien siga el proyecto |
| **Cambio de estado** (en curso, bloqueada, en verificación, resuelta, reabierta) | Igual que lo anterior |
| **Asignada** | La persona asignada (y quien tenía antes la tarea) |
| **Lista para verificar**, **verificada**, **rechazada** | Quien debe verificar / quien la resolvió |
| **Comentario** o **@mención** | Implicados y seguidores / la persona mencionada |
| **Chat**: mensaje nuevo | Según el modo de cada canal: todos, solo menciones y respuestas, o silenciado |
| **Chat**: respuesta a tu mensaje | Tú |
| **Tus pendientes** (cada mañana laborable, a la hora que elijas) | Tú: lo asignado, lo que espera tu verificación y lo que vence hoy |
| Algo lleva **más de un día esperando tu verificación** | Quien debe verificarlo (una vez al día) |
| Idea nueva / cambios en una idea | Proyecto / autor y quienes la votaron |
| Versión planificada, **publicada** o **en riesgo** (sale en ≤ 3 días con tareas abiertas) | Proyecto / responsables de planificación |
| Objetivo de la semana cumplido | Quien ve la planificación |
| Build nueva, prueba fallida, ronda de pruebas terminada | Calidad del proyecto |
| Página de la wiki nueva o editada, decisión propuesta, aceptada o rechazada | Quien ve el conocimiento / autor y seguidores |
| Ausencia de un compañero (al registrarla y el día antes) | Compañeros de sus proyectos |
| Fechas del calendario (el día antes y el mismo día) | Quien ve la planificación del proyecto |
| Tu rol cambia, te añaden a un proyecto, entran en tu cuenta desde un equipo nuevo | Tú |
| Alguien que sigues **se conecta o se desconecta** (más de 30 s fuera) | Quien lo sigue |
| Copia de seguridad fallida, poco espacio en disco, alguien quita su verificación en dos pasos | Administradores |

Reglas que se cumplen siempre:

- **Nunca te avisa de lo que haces tú.**
- **Un solo aviso por acción**: si ya te llega «Te han asignado T-012», no te llega además «T-012 ha cambiado».
- **Sin spam**: las ediciones seguidas de la misma persona sobre lo mismo (2 minutos) se juntan en un aviso; los
  mensajes de un canal se agrupan («3 mensajes nuevos en #general») y si llegan muchos avisos a la vez sale un resumen.

## Seguir

El botón **Seguir** está en cada tarea, incidencia y página de la wiki, y en «Tareas» puedes **seguir el proyecto
entero** (útil para el CTO o el CEO). Quien crea, recibe, comenta o edita algo lo sigue automáticamente.

## Quién está en línea

Junto a «Conectado», arriba, salen las personas que sigues con un punto verde si están en línea. Se eligen con «+»
(o «Ver en línea»), con la campana de cada persona en **Equipo** o en **Usuarios y roles**. Seguir a alguien también
te avisa cuando se conecta o se desconecta (un corte breve de menos de 30 segundos no cuenta, y al reiniciar el
anfitrión no se avisa de que todo el equipo vuelve a conectarse).

## Preferencias (Mi cuenta → Avisos)

- **Ajuste rápido**: *Todo*, *Lo importante* o *Solo lo mío*.
- Para cada tipo de aviso: **En la app** (se crea en la campana) y **Aviso de Windows**.
- **De qué tareas quieres enterarte**: todas las de tus proyectos o solo las tuyas y las que sigues.
- **Tus pendientes**: hora del resumen de cada mañana.
- **Horario de silencio** (por ejemplo, de 20:00 a 8:00): no salen avisos de Windows, salvo los urgentes.
- **No molestar** durante un tiempo.
- **Probar un aviso de Windows** para comprobar que se ven en ese equipo.
- En el chat, el icono de la campana de cada canal elige: todos los mensajes, solo menciones y respuestas, o silenciado.

## Cómo funciona por dentro

- El servidor crea los avisos a partir del historial de actividad (`server/watch.py`) y de las tareas programadas
  (`server/notifications.py`).
- El programa de cada equipo pregunta cada pocos segundos por los avisos nuevos de la persona conectada
  (`agent/notifier.py`) y los muestra con el sistema de avisos de Windows 10/11 (`toast.py`, sin dependencias nuevas).
  Si Windows no los permite, usa el globo del icono de la bandeja.
- Pulsar un aviso abre `devcontrol://aviso/<número>`, que lleva al programa (ya abierto o no) a ese aviso.
- Con la ventana delante no sale el aviso de Windows: se ve dentro del programa.
