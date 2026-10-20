"""Registros de diagnóstico sin contraseñas ni secretos."""
from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_SECRET_PATTERNS = [
    (re.compile(r'("?(?:password|contrasena|current|new_password|token)"?\s*[:=]\s*)"[^"]*"', re.I), r'\1"***"'),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.]+", re.I), r"\1***"),
    (re.compile(r"((?:token|access_token)=)[^&\s]+", re.I), r"\1***"),
]


def redact(text: str) -> str:
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logging(log_dir: Path, name: str) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("devcontrol")
    logger.setLevel(logging.INFO)
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    handler = RotatingFileHandler(log_dir / f"{name}.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s [%(threadName)s] %(message)s"))
    logger.addHandler(handler)
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger("devcontrol")
