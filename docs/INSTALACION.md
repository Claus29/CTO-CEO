# Instalación

## Requisitos

- Windows 10 u 11 (64 bits). La ventana usa WebView2, que ya viene con Windows 11 y con Windows 10 actualizado.
- Una red privada entre los equipos: la misma red local, una VPN o [Tailscale](https://tailscale.com) (recomendado
  si trabajáis desde sitios distintos: es gratuito para equipos pequeños y no hay que abrir puertos en el router).

## 1. Equipo anfitrión

El anfitrión guarda la base de datos y ejecuta el servidor del equipo. Debe estar encendido para que los demás trabajen
(si se apaga, los miembros siguen trabajando sin conexión y todo se envía al volver).

1. Ejecuta `DEVCONTROL-Setup-<versión>.exe` y elige **Anfitrión**.
2. Marca **Abrir DEV CONTROL al iniciar Windows**.
3. Solo si tienes datos anteriores que recuperar, marca **«Tengo datos de la versión anterior y quiero
   recuperarlos»** y elige en la pantalla siguiente la carpeta del programa anterior (la que contiene `datos`), una
   base de datos `.db` o una copia `.zip`. Si no la marcas, no se pide nada. Los datos se recuperan la primera vez
   que se abre el programa y solo si en ese equipo aún no hay datos (los identificadores de la versión 1 pasan al
   prefijo `INC-` y las referencias en los textos se actualizan).
4. Al abrirse, crea la cuenta de **administrador** y el primer proyecto (su prefijo numera las incidencias: `INC-001`).
5. Haz clic derecho en el icono de la bandeja → **Permitir en el Firewall de Windows** y acepta: abre el servidor y
   las llamadas (puertos 8765, 8766 y 50000–50199) solo para redes privadas.
6. Apunta la IP del anfitrión (con Tailscale, la `100.x.y.z` que muestra su icono).

## 2. Equipos miembros

1. Ejecuta el instalador y elige **Miembro**.
2. Escribe la IP o el nombre del anfitrión (con otro puerto: `100.1.2.3:9000`) y pulsa **Probar conexión**.
3. Inicia sesión con el usuario que te dio el administrador. La primera vez se te pedirá cambiar la contraseña.

En la pantalla de inicio de sesión puedes escribir **otro servidor** en cualquier momento; si lo dejas vacío se usa el
de por defecto. Los servidores usados se recuerdan, y cada uno tiene su propia cola de cambios sin conexión.

## 3. Invitar al equipo

En **Usuarios y roles** el administrador crea cada usuario con su rol, sus proyectos y una contraseña temporal.
Los roles son editables: cada permiso se comprueba en el servidor (ver [USO.md](USO.md)).

## Problemas frecuentes

| Síntoma | Solución |
|---|---|
| «El equipo responde, pero DEV CONTROL no está en marcha» | Abre DEV CONTROL en el anfitrión o pulsa **Iniciar servidor** en su icono de la bandeja. |
| «No hay respuesta» | El anfitrión está apagado, sin red o con la VPN desconectada. |
| «Este equipo no está autorizado» | La IP del miembro no está en las redes permitidas (`redes_permitidas` en `config.ini`). |
| SmartScreen bloquea el instalador | **Más información → Ejecutar de todas formas**. |
| Quiero comprobar la conexión | Ejecuta `"DEV CONTROL.exe" --diagnostico`: deja un informe en la carpeta de registros. |
