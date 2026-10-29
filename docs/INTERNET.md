# Acceso por Internet (túnel de Cloudflare)

Con un túnel de Cloudflare los demás pueden usar DEV CONTROL desde cualquier sitio, **sin VPN ni Tailscale**. El
anfitrión sigue siendo tu PC: los datos, los adjuntos y las copias de seguridad no salen de él. Cloudflare solo hace de
puente entre una dirección pública (`https://devcontrol.tudominio.com`) y el puerto de DEV CONTROL en tu equipo.

```
Equipo de un miembro ──https──▶ Cloudflare ──túnel──▶ cloudflared (en el anfitrión) ──▶ 127.0.0.1:8765 (DEV CONTROL)
```

El túnel es gratuito. Solo hace falta un dominio propio gestionado en Cloudflare (unos 10 € al año).

## Preparación (una sola vez, en el equipo anfitrión)

1. Entra en [dash.cloudflare.com](https://dash.cloudflare.com) → **Zero Trust** → **Networks** → **Tunnels** →
   **Create a tunnel**. Elige el tipo **Cloudflared** y ponle un nombre (por ejemplo `devcontrol`).
2. Elige **Windows**, descarga el instalador de `cloudflared` e instálalo. Después abre una ventana de comandos **como
   administrador** y pega el comando que te muestra Cloudflare (`cloudflared.exe service install eyJ...`). Así queda
   como servicio de Windows: arranca solo con el equipo, aunque no inicies sesión.
3. Pasa a **Public hostname** (o «Route traffic») y rellena:
   - **Subdomain**: `devcontrol`
   - **Domain**: tu dominio
   - **Service**: tipo `HTTP`, URL `127.0.0.1:8765` (el puerto de `[servidor] puerto` en `config.ini`; el panel
     de DEV CONTROL te muestra el correcto).

   Guarda. El túnel debe aparecer como **Healthy**.
4. En DEV CONTROL: **Ajustes → Acceso por Internet**. Escribe la dirección (`https://devcontrol.tudominio.com`), marca
   **Permitir entrar por Internet**, pulsa **Guardar** y después **Comprobar**. Si algo falta, el mensaje dice qué.
5. Recomendado: en **Ajustes → General y avisos**, activa la verificación en dos pasos para todos los roles.

## Lo que hacen los demás

En la pantalla de inicio de sesión, en **Servidor**, escriben `https://devcontrol.tudominio.com` y entran con su
usuario de siempre. No tienen que instalar nada más ni cambiar ningún ajuste. Las actualizaciones les siguen llegando
del anfitrión por esa misma dirección.

## Cómo funciona por dentro

- `cloudflared` entrega cada petición a DEV CONTROL desde `127.0.0.1` con la IP real de quien la hace en la cabecera
  `CF-Connecting-IP`. DEV CONTROL solo se fía de esa cabecera cuando llega desde el propio equipo.
- Esas peticiones se consideran **de Internet**:
  - solo se aceptan si el interruptor está activado (si no, responden «el anfitrión no permite entrar por Internet»);
  - nunca tienen los privilegios del equipo anfitrión (configuración inicial, modo recuperación, detener el
    servidor);
  - los intentos fallidos de inicio de sesión se cuentan por su IP real;
  - `/api/health` no muestra el nombre del equipo ni sus IP internas.
- La red local y Tailscale siguen funcionando igual a la vez.

## Limitaciones

- **Llamadas**: el audio y el vídeo usan UDP, que no pasa por el túnel. Desde Internet no se ofrecen las llamadas.
  Siguen funcionando en la red local o con Tailscale.
- **Adjuntos**: el plan gratuito de Cloudflare admite peticiones de hasta 100 MB, así que por Internet el máximo
  por archivo es de 95 MB.
- **El anfitrión debe estar encendido.** Mientras DEV CONTROL está abierto el equipo no se suspende (la pantalla sí se
  apaga); se puede desactivar con `mantener_despierto = no` en la sección `[escritorio]` de `config.ini`. Si el PC
  se apaga, los demás ven «el anfitrión no responde» y sus cambios se quedan en cola hasta que vuelva.

## Copias de seguridad en la nube

En **Ajustes → Copias de seguridad → Copia en la nube** elige una carpeta de OneDrive, Google Drive o Dropbox (el
programa sugiere las que encuentra en el equipo). Cada copia de seguridad se duplica allí y la aplicación de
sincronización la sube a la nube. En esa carpeta se conservan las mismas copias automáticas que en la carpeta
normal y las 10 últimas de los demás tipos. Si la copia a esa carpeta falla, la copia normal se hace igualmente y
los administradores reciben un aviso.

## Problemas frecuentes

| Mensaje | Qué hacer |
|---|---|
| «Esa dirección no existe todavía» | Revisa la *Public hostname* en Cloudflare. Tras crearla puede tardar unos minutos. |
| «Cloudflare no encuentra el túnel conectado» (530 / 1033) | El servicio `Cloudflared agent` no está en marcha: en `services.msc` inícialo, o repite el paso 2. Si tienes varios túneles, la *Public hostname* debe estar en el que aparece «Healthy». |
| «El túnel está conectado, pero cloudflared no consigue entrar en DEV CONTROL» (502) | El túnel sale «Healthy» pero la *Public hostname* apunta mal. Edítala: Service tipo `HTTP` (no `HTTPS`) y URL `127.0.0.1:8765` (no el `8080` del ejemplo). |
| «Esa dirección no lleva a DEV CONTROL» | La *Public hostname* debe apuntar a `HTTP` · `127.0.0.1:8765`. |
| «El túnel funciona… pero el acceso está desactivado» | Marca «Permitir entrar por Internet» y guarda. |
| El miembro ve «No hay respuesta de devcontrol…» | Ha escrito la dirección sin `https://` delante. |
