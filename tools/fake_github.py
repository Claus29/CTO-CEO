"""GitHub de mentira para las pruebas reales de actualización del anfitrión.

Uso: python tools/fake_github.py <puerto> <carpeta con manifest.json, manifest.json.sig y el instalador>
Responde a /repos/<dueño>/<repo>/releases/latest como la API de GitHub y sirve los archivos como «assets».
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(sys.argv[1])
FOLDER = Path(sys.argv[2])


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        base = f"http://127.0.0.1:{PORT}"
        if self.path.endswith("/releases/latest"):
            manifest = json.loads((FOLDER / "manifest.json").read_text(encoding="utf-8"))
            names = ["manifest.json", "manifest.json.sig", manifest["installer"]["name"]]
            body = json.dumps({"tag_name": f"v{manifest['version']}",
                               "assets": [{"name": n, "url": f"{base}/assets/{n}"} for n in names]}).encode()
            ctype = "application/json"
        elif self.path.startswith("/assets/"):
            target = FOLDER / Path(self.path).name
            if not target.is_file():
                self.send_error(404)
                return
            body, ctype = target.read_bytes(), "application/octet-stream"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        print(self.address_string(), self.command, self.path, flush=True)


ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
