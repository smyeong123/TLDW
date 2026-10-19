"""`tldw serve`: tiny local HTTP receiver for the browser extension's Watch Later snapshots.

Listens on 127.0.0.1 only. Every POST must carry the shared token in `X-TLDW-Token`.
  GET  /health    -> {"ok": true}
  POST /snapshot  -> {"new": n, "updated": m}   (body: snapshot JSON, see intake.py)
"""

from __future__ import annotations

import hmac
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tldw.intake import SnapshotError, ingest_snapshot
from tldw.state import State

log = logging.getLogger(__name__)

MAX_BODY_BYTES = 5 * 1024 * 1024  # a few thousand Watch Later items is well under 1 MB


def make_server(state_db: Path, token: str, port: int) -> ThreadingHTTPServer:
    if not token:
        raise ValueError("TLDW_EXTENSION_TOKEN is empty; set it in .env first")

    class Handler(BaseHTTPRequestHandler):
        server_version = "tldw"

        def log_message(self, fmt: str, *args: object) -> None:
            log.info("%s %s", self.address_string(), fmt % args)

        def _send(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self._cors()
            self.end_headers()
            self.wfile.write(body)

        def _cors(self) -> None:
            origin = self.headers.get("Origin", "")
            if origin.startswith("chrome-extension://"):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-TLDW-Token")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

        def do_OPTIONS(self) -> None:  # noqa: N802 - http.server naming
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                self._send(200, {"ok": True})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/snapshot":
                self._send(404, {"error": "not found"})
                return
            given = self.headers.get("X-TLDW-Token", "")
            if not hmac.compare_digest(given.encode(), token.encode()):
                self._send(401, {"error": "bad token"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY_BYTES:
                self._send(413 if length > 0 else 400, {"error": "bad body size"})
                return
            try:
                data = json.loads(self.rfile.read(length))
                with State(state_db) as state:
                    result = ingest_snapshot(state, data)
            except (json.JSONDecodeError, SnapshotError) as e:
                self._send(400, {"error": str(e)})
                return
            log.info("snapshot: %d new, %d updated", result.new, result.updated)
            self._send(200, {"new": result.new, "updated": result.updated})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
