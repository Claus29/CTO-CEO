"""Punto de entrada del ejecutable empaquetado (PyInstaller)."""
import sys

from devcontrol.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
