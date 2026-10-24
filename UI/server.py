"""Serve the local practice UI: python -m UI.server --port 8765."""

from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlsplit

from UI.practice import PracticeSession, RevisionConflict
from UI.game_session import DeveloperSession


STATIC_ROOT = Path(__file__).parent / "static"
PICTURES_ROOT = Path(__file__).resolve().parent.parent / "pictures"
TILE_IMAGES = {f"{value}.png" for value in range(21)} | {"24.png", "plus.png", "mult.png", "app_icon.png"}


class PracticeServer(ThreadingHTTPServer):
    """Advance timers and bots even when no browser is polling."""
    def __init__(self, address, handler, session):
        self.session = session
        super().__init__(address, partial(handler, session=session))

    def service_actions(self):
        with self.session.lock:
            self.session.tick()


class PracticeHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, session: PracticeSession, **kwargs):
        self.session = session
        super().__init__(*args, directory=str(STATIC_ROOT), **kwargs)

    def _json(self, status: int, body: dict) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/state":
            with self.session.lock:
                self._json(200, self.session.snapshot())
        elif path.startswith("/tiles/"):
            filename = path.removeprefix("/tiles/")
            if filename not in TILE_IMAGES:
                self.send_error(404)
                return
            payload = (PICTURES_ROOT / filename).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            super().do_GET()

    def do_POST(self):
        if self.path != "/api/action":
            self._json(404, {"error": "接口不存在"})
            return
        origin = self.headers.get("Origin")
        if origin is not None and origin != f"http://{self.headers.get('Host')}":
            self._json(403, {"error": "仅允许本地页面操作"})
            return
        try:
            if self.headers.get_content_type() != "application/json":
                raise ValueError("请使用 JSON 请求")
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > 65536:
                raise ValueError("请求大小不合法")
            command = json.loads(self.rfile.read(size))
            if not isinstance(command, dict):
                raise ValueError("请求必须是 JSON 对象")
            with self.session.lock:
                state = self.session.dispatch(command)
            self._json(200, {"state": state})
        except (ValueError, KeyError, TypeError) as exc:
            with self.session.lock:
                self._json(409 if isinstance(exc, RevisionConflict) else 400, {
                    "error": str(exc), "state": self.session.snapshot(),
                })


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    session = DeveloperSession()
    with PracticeServer(("127.0.0.1", args.port), PracticeHandler, session) as server:
        print(f"Local: http://127.0.0.1:{args.port}", flush=True)
        server.serve_forever(poll_interval=0.1)


if __name__ == "__main__":
    main()
