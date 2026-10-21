# Llamadas de voz y vídeo

## Qué son

Llamadas entre **cuentas** de DEV CONTROL, de 2 personas o de grupo (hasta 8, con 4 cámaras a la vez), con
compartir pantalla. Se llama a una persona, no a un ordenador: suena en todos los equipos donde tenga DEV CONTROL
abierto y la llamada sigue en el primero en el que conteste. Funciona siempre que los participantes puedan usar el
programa, desde cualquier red que llegue al anfitrión (Tailscale, red local, VPN…).

## Cómo se usa

- **Llamar a una persona:** botón 📞 de la barra superior (panel de Llamadas), las tarjetas de Equipo o el Panel.
- **Llamada de grupo:** en el panel de Llamadas marca varias personas, o pulsa 📞 en la cabecera de un canal del chat
  («Llamar al canal», que avisa a sus miembros conectados; los demás pueden unirse después desde el panel).
- **Durante la llamada:** silenciar, cámara, compartir pantalla, añadir personas, elegir micrófono/altavoz/cámara,
  ampliar o reducir y colgar. Se ve quién habla, quién tiene el micrófono silenciado y la calidad de cada conexión
  (directa o a través del anfitrión).
- **Al recibir una llamada:** suena, aparece un aviso de Windows y la ventana se pone delante aunque estuviera oculta.
  Si no se contesta en 45 segundos queda como **llamada perdida**. Con «No molestar» activo no suena.
- **Historial:** panel de Llamadas → Historial (con «Devolver llamada»). En los canales queda un mensaje con la
  duración y los participantes.

## Cómo funciona

- El **anfitrión** hace de centralita: avisa de las llamadas y ayuda a que los equipos se conecten.
- La voz y el vídeo van **directos** entre los equipos cuando pueden verse. Si no pueden, pasan por el **repetidor**
  integrado en el anfitrión (TURN), de forma automática. El contenido va **cifrado de extremo a extremo** (WebRTC):
  el anfitrión no puede escucharlo. No se graba nada.
- Si alguien pierde la red, la llamada intenta reconectarse sola; si en 20 segundos su equipo no responde, sale de la
  llamada sin bloquear a los demás. Si se detiene el servidor, las llamadas se cierran con un aviso.

## Puertos (solo en el anfitrión)

| Puerto | Uso |
|---|---|
| 8765 TCP | Servidor de DEV CONTROL |
| 8766 UDP y TCP | Repetidor de llamadas (`puerto_llamadas` en config.ini) |
| 50000–50199 UDP | Reenvío de audio y vídeo (`puertos_reenvio`) |

Tras instalar o actualizar, en el anfitrión: clic derecho en el icono de la bandeja → **Permitir en el Firewall de
Windows** y acepta. Abre estos puertos solo para redes privadas.

## Problemas frecuentes

| Síntoma | Solución |
|---|---|
| No aparece el botón de llamadas | El anfitrión tiene una versión antigua: debe actualizar DEV CONTROL. |
| «No hay permiso para usar el micrófono» | Configuración de Windows → Privacidad → Micrófono: permite el acceso a las aplicaciones de escritorio. |
| La llamada suena pero no se oye nada / se queda «Reconectando…» | En el anfitrión, «Permitir en el Firewall de Windows» desde el icono de la bandeja. |
| «Ya estás en una llamada» tras cerrar la ventana de golpe | Espera unos segundos (el servidor lo detecta en 20 s) o pulsa «Volver a la llamada» / «Colgar». |
