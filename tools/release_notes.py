"""Extrae del CHANGELOG.md las notas de la versión actual (para la publicación)."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from devcontrol import APP_VERSION  # noqa: E402

text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
m = re.search(rf"^## \[?{re.escape(APP_VERSION)}\]?.*?$(.*?)(?=^## |\Z)", text, re.S | re.M)
print(m.group(1).strip() if m else f"DEV CONTROL {APP_VERSION}")
