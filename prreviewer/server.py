"""`review.py view --id <id>`: serve the editor on 127.0.0.1 and open a browser.

Endpoints:
  GET  /               the editor (re-rendered from review.json on every load)
  GET  /api/review     current review.json
  POST /api/save       replace review.json with the posted body (validated)
  GET  /api/diff       parsed diff
"""
from __future__ import annotations

import json
import socket
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional

from . import log
from .render import render_review_html
from .schema import Review, SEVERITIES, SIDES, VERDICTS
from .store import ReviewStore


def _free_port(preferred: int = 0) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferred))
        except OSError:
            s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def validate_review_payload(d: Dict[str, Any]) -> Review:
    if not isinstance(d, dict) or "id" not in d or "findings" not in d:
        raise ValueError("payload must be a review object with id and findings")
    r = Review.from_dict(d)
    if r.verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}")
    for f in r.findings:
        if f.severity not in SEVERITIES:
            raise ValueError(f"finding {f.id}: bad severity {f.severity!r}")
        if f.side not in SIDES:
            f.side = "RIGHT"
        if f.status not in ("active", "cut"):
            f.status = "active"
        if f.line is not None:
            f.line = int(f.line)
        if f.start_line is not None:
            f.start_line = int(f.start_line)
    return r


def make_handler(store: ReviewStore, rid: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quieter
            log.debug("http " + (fmt % args))

        def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                review = store.load_review(rid)
                diff = store.read_json(rid, "diff.json", {"files": []})
                html = render_review_html(review, diff, served=True)
                store.write_html(rid, html)
                self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            elif self.path == "/api/review":
                self._send(200, json.dumps(store.load_review(rid).to_dict()).encode("utf-8"))
            elif self.path == "/api/diff":
                self._send(200, json.dumps(store.read_json(rid, "diff.json", {"files": []})).encode("utf-8"))
            else:
                self._send(404, b'{"error":"not found"}')

        def do_POST(self):
            if self.path != "/api/save":
                self._send(404, b'{"error":"not found"}')
                return
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n)
            try:
                review = validate_review_payload(json.loads(raw.decode("utf-8")))
                if review.id != rid:
                    raise ValueError("id mismatch")
            except (ValueError, KeyError) as e:
                self._send(400, json.dumps({"error": str(e)}).encode("utf-8"))
                return
            store.save_review(review)
            diff = store.read_json(rid, "diff.json", {"files": []})
            store.write_html(rid, render_review_html(review, diff, served=False))
            self._send(200, b'{"ok":true}')

    return Handler


def serve(store: ReviewStore, rid: str, port: int = 0, open_browser: bool = True, block: bool = True) -> ThreadingHTTPServer:
    if not store.exists(rid):
        raise FileNotFoundError(f"no local review with id {rid!r} (see `python review.py list`)")
    port = _free_port(port)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(store, rid))
    url = f"http://127.0.0.1:{port}/"
    log.info(f"viewing {rid} at {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    if block:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()
    else:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd
