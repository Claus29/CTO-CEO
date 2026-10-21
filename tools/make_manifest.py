"""Genera y firma manifest.json para una versión publicada.

Uso: python tools/make_manifest.py dist/DEVCONTROL-Setup-2.0.1.exe --repo usuario/repo --notes NOTAS.md
La clave privada se lee de la variable de entorno DEVCONTROL_SIGNING_KEY (secreto de GitHub Actions).
Escribe dist/manifest.json y dist/manifest.json.sig.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from devcontrol import APP_NAME, APP_VERSION  # noqa: E402
from devcontrol.updater import sign_manifest, verify_manifest  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("installer")
    p.add_argument("--repo", required=True)
    p.add_argument("--notes", default="")
    p.add_argument("--min-version", default=None)
    args = p.parse_args()
    key = os.environ.get("DEVCONTROL_SIGNING_KEY", "").strip()
    if not key:
        sys.exit("Falta el secreto DEVCONTROL_SIGNING_KEY: sin él las actualizaciones no se pueden firmar.")
    if key.startswith(("github_pat_", "ghp_")) or len(key) != 44 or not key.endswith("="):
        sys.exit("El secreto DEVCONTROL_SIGNING_KEY no contiene la clave de firma (un texto de 44 caracteres que "
                 "termina en «=»). La clave de acceso github_pat_… no va ahí: va en Ajustes del anfitrión.")
    inst = Path(args.installer)
    data = inst.read_bytes()
    notes = Path(args.notes).read_text(encoding="utf-8") if args.notes and Path(args.notes).exists() else args.notes
    manifest = {
        "app": APP_NAME, "version": APP_VERSION, "min_version": args.min_version,
        "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "notes": notes.strip(),
        "installer": {"name": inst.name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                      "url": f"https://github.com/{args.repo}/releases/download/v{APP_VERSION}/{inst.name}"},
    }
    raw = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    sig = sign_manifest(raw, key)
    verify_manifest(raw, sig)  # comprueba que la clave corresponde a la pública del programa
    out = inst.parent
    (out / "manifest.json").write_bytes(raw)
    (out / "manifest.json.sig").write_text(sig, encoding="ascii")
    print(f"Manifiesto firmado para {APP_VERSION}: {inst.name} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
