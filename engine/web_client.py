"""
web_client.py -- static browser play page served on the WebSocket port.

Plain HTTP GET requests on ``RIFTFORGE_WS_PORT`` (default 4080) receive
``index.html``, ``app.js``, ``style.css``, and ``discord-invite.js``.
``GET /discord.json`` and ``GET /status.json`` serve gitignored sidecars
the game writes (login Discord invite; who-count + last player-facing ship).
WebSocket upgrades on the same port are handled by ``engine.gateway``
(not this module).

Stdlib only. See ``docs/plans/browser_websocket_client.md``.
"""

from __future__ import annotations

import mimetypes
import os

# Defensive cap aligned with engine.websocket handshake limit.
MAX_HTTP_REQUEST_BYTES = 8192

_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_client", "static")

# Only these paths are served — no directory traversal.
_ALLOWED: dict[str, str] = {
    "/": "index.html",
    "/index.html": "index.html",
    "/app.js": "app.js",
    "/style.css": "style.css",
    "/discord-invite.js": "discord-invite.js",
}


def _repo_sidecar_path(style_attr: str, default: str) -> str:
    """Repo-root gitignored JSON nginx aliases on the public site."""
    from engine import style as style_mod

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    name = getattr(style_mod, style_attr, default)
    return os.path.normpath(os.path.join(root, name))


def _discord_invite_json_path() -> str:
    """Repo-root ``.discord_invite.json`` (same file nginx aliases on live)."""
    return _repo_sidecar_path("LOGIN_DISCORD_INVITE_JSON", ".discord_invite.json")


def _public_status_json_path() -> str:
    """Repo-root ``.public_status.json`` (same file nginx aliases on live)."""
    return _repo_sidecar_path("LOGIN_PUBLIC_STATUS_JSON", ".public_status.json")


def static_dir() -> str:
    """Absolute path to bundled static assets (tests / ops)."""
    return _STATIC_DIR


def _http_response(
    code: int,
    reason: str,
    body: bytes,
    *,
    content_type: str = "text/plain; charset=utf-8",
    extra_headers: dict[str, str] | None = None,
) -> bytes:
    """Build a minimal HTTP/1.1 response (connection: close)."""
    headers = {
        "Content-Type": content_type,
        "Content-Length": str(len(body)),
        "Connection": "close",
        "Cache-Control": "no-cache",
    }
    if extra_headers:
        headers.update(extra_headers)
    head = f"HTTP/1.1 {code} {reason}\r\n"
    for key, value in headers.items():
        head += f"{key}: {value}\r\n"
    head += "\r\n"
    return head.encode("ascii") + body


def _parse_get_path(raw: bytes) -> str | None:
    """Return the URL path from a complete HTTP request, or None if invalid."""
    if len(raw) > MAX_HTTP_REQUEST_BYTES:
        return None
    if b"\r\n\r\n" not in raw:
        return None
    head = raw.split(b"\r\n\r\n", 1)[0]
    try:
        text = head.decode("latin-1")
    except UnicodeDecodeError:
        return None
    lines = text.split("\r\n")
    if not lines:
        return None
    parts = lines[0].split()
    if len(parts) < 2 or parts[0].upper() != "GET":
        return None
    path = parts[1].split("?", 1)[0]
    if not path.startswith("/"):
        return None
    return path


def serve_http(raw: bytes) -> bytes:
    """Answer one HTTP GET with a static file or a small error page."""
    path = _parse_get_path(raw)
    if path is None:
        return _http_response(400, "Bad Request", b"Bad request")
    if path == "/discord.json":
        full = _discord_invite_json_path()
        if not os.path.isfile(full):
            return _http_response(404, "Not Found", b"Not found")
        with open(full, "rb") as fh:
            body = fh.read()
        return _http_response(
            200, "OK", body,
            content_type="application/json; charset=utf-8",
        )
    if path == "/status.json":
        full = _public_status_json_path()
        if not os.path.isfile(full):
            return _http_response(404, "Not Found", b"Not found")
        with open(full, "rb") as fh:
            body = fh.read()
        return _http_response(
            200, "OK", body,
            content_type="application/json; charset=utf-8",
        )
    rel = _ALLOWED.get(path)
    if rel is None:
        return _http_response(404, "Not Found", b"Not found")
    full = os.path.normpath(os.path.join(_STATIC_DIR, rel))
    static_root = os.path.normpath(_STATIC_DIR)
    if not full.startswith(static_root + os.sep) and full != static_root:
        return _http_response(403, "Forbidden", b"Forbidden")
    if not os.path.isfile(full):
        return _http_response(500, "Internal Server Error", b"Missing static file")
    with open(full, "rb") as fh:
        body = fh.read()
    ctype = mimetypes.guess_type(rel)[0] or "application/octet-stream"
    if rel.endswith(".js"):
        ctype = "application/javascript; charset=utf-8"
    elif rel.endswith(".css"):
        ctype = "text/css; charset=utf-8"
    elif rel.endswith(".html"):
        ctype = "text/html; charset=utf-8"
    return _http_response(200, "OK", body, content_type=ctype)
