"""UTF-8 Static HTTP Server for RatePath (ريت باث) Frontend on Windows.

Serves `/frontend` on port 8080 with strict UTF-8 MIME headers so ES Modules,
Service Worker (`sw.js`), `manifest.json`, and `/public/live_feed.json` work reliably.
"""

from __future__ import annotations

import os
import socket
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

FRONTEND_DIR: Path = Path(__file__).resolve().parent


class DualStackHTTPServer(ThreadingHTTPServer):
    """Dual-stack IPv6/IPv4 HTTP server so both `::1` and `127.0.0.1` resolve `localhost` instantly."""

    address_family = socket.AF_INET6

    def server_bind(self) -> None:
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


class RatePathRequestHandler(SimpleHTTPRequestHandler):
    """UTF-8 MIME-hardened static file handler for `/frontend`."""

    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        "": "application/octet-stream",
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".mjs": "application/javascript; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".webmanifest": "application/manifest+json; charset=utf-8",
        ".svg": "image/svg+xml; charset=utf-8",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".ico": "image/x-icon",
    }

    def translate_path(self, path: str) -> str:
        clean_path = unquote(urlsplit(path).path)
        if clean_path in ("/ar", "/ar/", "/en", "/en/"):
            return str(FRONTEND_DIR / "index.html")
        # Support `/frontend/public/live_feed.json`, `/public/live_feed.json`, and `/live_feed.json`
        if clean_path in ("/frontend/public/live_feed.json", "/live_feed.json"):
            return str(FRONTEND_DIR / "public" / "live_feed.json")
        if clean_path.startswith("/frontend/"):
            clean_path = clean_path[len("/frontend") :]
        return super().translate_path(clean_path)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Service-Worker-Allowed", "/")
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()


def run_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    os.environ.setdefault("PYTHONUTF8", "1")
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    handler = partial(RatePathRequestHandler, directory=str(FRONTEND_DIR))
    try:
        server: ThreadingHTTPServer = DualStackHTTPServer(("::", port), handler)
    except OSError:
        server = ThreadingHTTPServer((host, port), handler)

    with server as httpd:
        print(
            f"[RatePath | ريت باث] Serving {FRONTEND_DIR} at http://localhost:{port}",
            flush=True,
        )
        httpd.serve_forever()


if __name__ == "__main__":
    bind_port = int(os.environ.get("PORT", "8765"))
    run_server(host="127.0.0.1", port=bind_port)
