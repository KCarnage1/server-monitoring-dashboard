from __future__ import annotations

import hmac
import ipaddress
import json
import mimetypes
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .collector import Sampler

WEB = Path(__file__).parent / "web"
STATIC = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/lib.js": "lib.js", "/style.css": "style.css"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
CSP = "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'"


def is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


class Handler(BaseHTTPRequestHandler):
    server: "MonitorServer"
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:
        pass

    def _send(self, status: int, body: bytes, ctype: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj) -> None:
        self._send(status, json.dumps(obj).encode(), "application/json")

    def _allowed_host(self) -> bool:
        # A page on another origin could point its own hostname at 127.0.0.1 and read the data, so only expected Host values pass.
        if self.server.token:
            return True
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0] if not (self.headers.get("Host") or "").startswith("[") else \
            (self.headers.get("Host") or "").split("]")[0] + "]"
        return host in LOCAL_HOSTS or host in self.server.extra_hosts

    def _authorized(self, query: dict[str, list[str]]) -> bool:
        tok = self.server.token
        if not tok:
            return True
        supplied = query.get("token", [""])[0]
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            supplied = auth[7:]
        return hmac.compare_digest(supplied.encode(), tok.encode())

    def do_GET(self) -> None:
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if not self._allowed_host():
            return self._json(HTTPStatus.FORBIDDEN, {"error": "Unexpected Host header."})
        if url.path in STATIC:
            name = STATIC[url.path]
            ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
            return self._send(200, (WEB / name).read_bytes(), ctype + "; charset=utf-8")
        if url.path.startswith("/api/"):
            if not self._authorized(query):
                return self._json(HTTPStatus.UNAUTHORIZED, {"error": "A valid token is required."})
            s = self.server.sampler
            if url.path == "/api/health":
                return self._json(200, {"ok": True, "interval": s.interval, "samples": len(s.history())})
            if url.path == "/api/info":
                return self._json(200, {**s.collector.host, "interval": s.interval, "history": s.buffer.maxlen,
                                        "thresholds": {"warn": s.collector.thresholds.warn, "crit": s.collector.thresholds.crit},
                                        "processes": s.collector.include_processes})
            if url.path == "/api/snapshot":
                return self._json(200, s.latest())
            if url.path == "/api/history":
                return self._json(200, s.history())
            if url.path == "/api/stream":
                return self._stream(s)
        self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})

    def _stream(self, sampler: Sampler) -> None:
        self.send_response(200)
        for k, v in {"Content-Type": "text/event-stream", "Cache-Control": "no-store", "Connection": "keep-alive",
                     "Content-Security-Policy": CSP, "X-Content-Type-Options": "nosniff"}.items():
            self.send_header(k, v)
        self.end_headers()
        seq = int(self.headers.get("Last-Event-ID") or 0)
        try:
            self.wfile.write(b"retry: 3000\n\n")
            while not self.server.closing.is_set():
                snap = sampler.wait_for(seq, timeout=10)
                if snap is None:
                    self.wfile.write(b": keepalive\n\n")
                else:
                    seq = snap["seq"]
                    self.wfile.write(f"id: {seq}\ndata: {json.dumps(snap)}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return


class MonitorServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr: tuple[str, int], sampler: Sampler, token: str | None = None, extra_hosts: set[str] | None = None):
        super().__init__(addr, Handler)
        self.sampler = sampler
        self.token = token or None
        self.extra_hosts = extra_hosts or set()
        self.closing = threading.Event()

    def shutdown(self) -> None:
        self.closing.set()
        with self.sampler.cond:
            self.sampler.cond.notify_all()
        super().shutdown()
