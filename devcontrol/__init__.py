"""DEV CONTROL: coordinación y control de un equipo de desarrollo de apps."""

APP_NAME = "DEV CONTROL"
APP_ID = "DevControl"
APP_VERSION = "2.4.0"
# Versión del protocolo entre clientes y servidor. Se incrementa cuando un
# cambio de la API deja de ser compatible con versiones anteriores.
PROTOCOL_VERSION = 3
# Versión mínima de cliente que acepta este servidor.
MIN_CLIENT_VERSION = "2.1.0"
DEFAULT_REPOSITORY = "Claus29/CTO-CEO"


def parse_version(value: str) -> tuple[int, int, int]:
    parts = []
    for piece in (value or "0").split(".")[:3]:
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits or 0))
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)  # type: ignore[return-value]
