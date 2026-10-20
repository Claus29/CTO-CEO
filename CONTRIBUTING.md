# Contribuir

¡Gracias por ayudar! DEV CONTROL busca ser sencillo de instalar y muy difícil de romper.

1. Abre una incidencia describiendo el problema o la propuesta (qué problema resuelve antes que la solución).
2. Crea una rama, haz el cambio y añade pruebas en `tests/`.
3. Comprueba en local:
   ```bash
   python -m pip install -r requirements-dev.txt
   python -m ruff check .
   python -m pytest
   for f in $(find devcontrol/web/js -name '*.js'); do node --check "$f"; done
   ```
4. Abre la petición de cambios explicando el porqué y cómo lo has probado.

## Normas

- Interfaz y mensajes en español claro, pensados para personas no técnicas.
- El servidor solo usa la biblioteca estándar y `cryptography`; la interfaz no usa dependencias externas ni `innerHTML`.
- Toda comprobación de permisos se hace en el servidor.
- Nunca se borran datos del usuario sin una copia previa; los cambios de esquema van en `server/migrations.py` con prueba.
- Las altas deben ser idempotentes (`uid`) para que la cola sin conexión no duplique nada.
