"""Browser interface for the local single-player runtime."""

import asyncio
from contextlib import asynccontextmanager
import json
import logging
from pathlib import Path
from threading import Event, Thread

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from singleplayer.practice import PracticeBusy, PracticeService


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "UI" / "static"
TILE_NAMES = {f"{i}.png" for i in range(21)} | {"24.png", "plus.png", "mult.png", "app_icon.png"}
ASSETS = {"icons.svg", "tile-back.svg", "LICENSE-lucide.txt"}


def create_app(runtime, *, port=8772, practice_service=None):
    practice = practice_service if practice_service is not None else PracticeService()
    @asynccontextmanager
    async def lifespan(app):
        stop = Event()

        def advance():
            while not stop.wait(0.1):
                try:
                    runtime.tick()
                except Exception:
                    app.state.runtime_failed = True
                    logging.getLogger(__name__).exception("Single-player runtime stopped")
                    return

        worker = Thread(target=advance, name="singleplayer-tick", daemon=True)
        worker.start()
        try:
            yield
        finally:
            stop.set()
            await asyncio.to_thread(worker.join)
            await asyncio.to_thread(practice.close)
            await run_in_threadpool(runtime.close)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.runtime = runtime
    app.state.practice = practice
    app.state.runtime_failed = False
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}

    @app.middleware("http")
    async def local_requests(request, call_next):
        origin = request.headers.get("origin")
        if origin is not None and origin not in origins:
            return JSONResponse({"error": "只接受本机页面操作"}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"error": "只接受本机页面操作"}, status_code=403)
        if request.client and request.client.host not in {"127.0.0.1", "::1", "testclient"}:
            return JSONResponse({"error": "仅限本机使用"}, status_code=403)
        response = await call_next(request)
        # Tile images are reused throughout a round. API state must stay fresh,
        # but recreating an image must not trigger another local file request.
        if request.url.path.startswith(("/tiles/", "/assets/")) and response.status_code == 200:
            response.headers["Cache-Control"] = "private, max-age=86400"
        else:
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'; frame-ancestors 'none'"
        return response

    def healthy():
        if app.state.runtime_failed:
            raise HTTPException(503, "单机程序已暂停，请重启")

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    @app.get("/healthz")
    def health():
        healthy()
        return {"ok": True, "instance_id": runtime.instance_id}

    @app.get("/api/state")
    def state():
        healthy()
        with runtime.lock:
            runtime.tick()
            return runtime.view()

    async def read_command(request):
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise HTTPException(415, "请提交 JSON 操作")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 16384:
                raise HTTPException(413, "操作内容过大")
        try:
            command = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
            if type(command) is not dict:
                raise ValueError()
        except (ValueError, UnicodeError, RecursionError):
            raise HTTPException(400, "请提交有效 JSON 对象") from None
        return command

    @app.post("/api/action")
    async def action(request: Request):
        healthy()
        command = await read_command(request)
        try:
            return {"state": await run_in_threadpool(runtime.dispatch, command)}
        except (ValueError, KeyError, TypeError) as exc:
            return JSONResponse({"error": str(exc) if isinstance(exc, ValueError) else "操作参数不正确",
                                 "state": runtime.view()}, status_code=400)

    @app.post("/api/practice")
    async def practice_action(request: Request):
        command = await read_command(request)
        try:
            kind = command.get("action")
            if kind == "new":
                question = await practice.new_question(command.get("seconds", 30))
            else:
                question_id = command.get("question_id")
                if type(question_id) is not str or not 0 < len(question_id) <= 100:
                    raise ValueError("请指定当前练习题")
                if kind == "start":
                    question = practice.start(question_id)
                elif kind in {"draft", "submit"}:
                    question = practice.answer(question_id, command.get("tiles"), command.get("not_ready"),
                                               command.get("revision"), submit=kind == "submit")
                elif kind == "reveal":
                    question = practice.reveal(question_id)
                else:
                    raise ValueError("未知的练习操作")
            return {"question": question}
        except PracticeBusy as exc:
            raise HTTPException(429, str(exc)) from None
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(400, str(exc) if isinstance(exc, ValueError) else "练习参数不正确") from None

    @app.get("/api/practice/{question_id}")
    def practice_question(question_id: str):
        try:
            return {"question": practice.get(question_id)}
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from None

    @app.get("/api/wall-record")
    def wall(round_id: str):
        healthy()
        try:
            return {"wall_record": runtime.wall_record(round_id=round_id)}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.get("/")
    @app.get("/index.html")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/{path:path}")
    def asset(path: str):
        if path in {"app.js", "client.js", "practice.js", "styles.css", "singleplayer.css"}:
            return FileResponse(STATIC / path)
        if path.startswith("tiles/") and path[6:] in TILE_NAMES:
            return FileResponse(ROOT / "pictures" / path[6:])
        if path.startswith("assets/") and path[7:] in ASSETS:
            return FileResponse(STATIC / "assets" / path[7:])
        raise HTTPException(404, "接口或资源不存在")

    return app
