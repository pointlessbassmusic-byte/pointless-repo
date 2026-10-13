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


PREFIX = "/sportsbot"

# The desk switcher. Behind Tailscale the board sits at /sportsbot beside the
# arbitrage desk's dashboard at /, so one strip at the top of each page lets a
# phone or desktop flip between them. It is added here, at serve time, only
# for requests that arrive under the prefix: the rendered files stay exactly
# as `sportsbot board` wrote them (script-free; also published as the
# artifact, where there is no desk at /). No JavaScript: three links and a
# style block.
_SWITCHER_CSS = (
    "<style>.hub{display:flex;gap:6px;margin:12px 16px 0;padding:4px;width:max-content;"
    "border:1px solid #e3e6ea;border-radius:999px;background:#fff;"
    "font:14px/1.3 ui-sans-serif,system-ui,-apple-system,'Segoe UI',Roboto,sans-serif}"
    ".hub a,.hub span{padding:6px 14px;border-radius:999px;color:#697386;text-decoration:none}"
    ".hub .current{background:#2563eb;color:#fff}.hub a:hover{color:#12151a;background:#eef1f5}"
    "@media(prefers-color-scheme:dark){.hub{border-color:#252b34;background:#161a21}"
    ".hub a,.hub span{color:#9aa4b2}.hub a:hover{color:#e9edf3;background:#1e242d}}</style>")

_TABS = (("Crypto desk", "/", None),
         ("Sports book", PREFIX + "/", "board.html"),
         ("Substrate", PREFIX + "/dashboard.html", "dashboard.html"))


def switcher(current: str) -> str:
    items = []
    for label, href, name in _TABS:
        if name == current:
            items.append(f'<span class="current">{label}</span>')
        else:
            items.append(f'<a href="{href}">{label}</a>')
    return _SWITCHER_CSS + '<nav class="hub">' + "".join(items) + "</nav>"


def with_switcher(body: bytes, name: str, request_path: str) -> bytes:
    """Insert the switcher after the opening <body> tag when the page is being
    served under PREFIX; otherwise return the file untouched."""
    if not request_path.startswith(PREFIX):
        return body
    lower = body.lower()
    i = lower.find(b"<body")
    if i < 0:
        return switcher(name).encode("utf-8") + body
    j = lower.find(b">", i)
    if j < 0:
        return body
    return body[:j + 1] + switcher(name).encode("utf-8") + body[j + 1:]


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
            body = with_switcher(body, name, self.path)
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
