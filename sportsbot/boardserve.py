"""Serve the board (and only the board) on localhost for a Tailscale path.

The arbitrage desk already runs Tailscale Serve on the same server, which
gives one private HTTPS address for phone and desktop. This serves
sportsbot's rendered pages on a localhost port so that node can proxy
them on a path next to the desk's dashboard:

    sudo tailscale serve --bg --set-path /sportsbot http://127.0.0.1:8001

Only a whitelist of rendered files is served (the data directory also
holds the SQLite store, which is nobody's business), nothing is listed,
nothing is written, and the socket binds to 127.0.0.1 only. No auth is
added here because Tailscale is the auth: the address is reachable only
by devices signed into the tailnet.
"""

from __future__ import annotations

import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

log = logging.getLogger(__name__)

ALLOWED = {"board.html": "text/html; charset=utf-8",
           "dashboard.html": "text/html; charset=utf-8"}
INDEX = "board.html"


def resolve(path: str) -> Optional[str]:
    """Map a request path to a whitelisted file name, or None."""
    name = path.split("?", 1)[0].split("#", 1)[0].strip("/")
    if name.startswith("sportsbot/"):          # behind `--set-path /sportsbot`
        name = name[len("sportsbot/"):]
    if name in ("", "sportsbot"):
        name = INDEX
    return name if name in ALLOWED else None


def make_handler(directory: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "sportsbot-board/1"

        def do_GET(self):          # noqa: N802 — stdlib name
            name = resolve(self.path)
            if name is None:
                self.send_error(404, "not here")
                return
            full = os.path.join(directory, name)
            try:
                with open(full, "rb") as fh:
                    body = fh.read()
            except OSError:
                self.send_error(503, "page not rendered yet")
                return
            self.send_response(200)
            self.send_header("Content-Type", ALLOWED[name])
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_HEAD(self):         # noqa: N802
            name = resolve(self.path)
            if name is None or not os.path.exists(os.path.join(directory, name)):
                self.send_error(404, "not here")
                return
            self.send_response(200)
            self.send_header("Content-Type", ALLOWED[name])
            self.end_headers()

        def log_message(self, fmt, *args):   # quiet; journald has enough
            log.debug("boardserve: " + fmt, *args)

    return Handler


def serve(directory: str = "data", port: int = 8001, host: str = "127.0.0.1") -> None:
    httpd = ThreadingHTTPServer((host, port), make_handler(directory))
    log.info("serving %s on http://%s:%d/ (whitelist: %s)", directory, host, port,
             ", ".join(sorted(ALLOWED)))
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
