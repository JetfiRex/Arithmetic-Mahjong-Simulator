"""Single-worker ASGI hosting entry; reset every boot, no Cloudflare identity yet."""

import argparse
import asyncio
from contextlib import asynccontextmanager
import json
import logging
from pathlib import Path
from threading import Event, Thread

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from multiplayer.rooms import RoomError
from multiplayer.runtime import LiveRooms
from multiplayer.security import IngressMiddleware, RateLimits, WebSettings


ROOT = Path(__file__).resolve().parents[1]
SHARED_STATIC = ROOT / "UI" / "static"
LOCAL_STATIC = Path(__file__).parent / "static"
PICTURES = ROOT / "pictures"
TILE_NAMES = {f"{i}.png" for i in range(21)} | {"24.png", "plus.png", "mult.png", "app_icon.png"}
ASSETS = {"icons.svg", "tile-back.svg", "LICENSE-lucide.txt"}


def create_app(*, settings=None, runtime=None):
    settings = settings or WebSettings(("http://127.0.0.1:8771", "http://localhost:8771"))
    rooms = runtime or LiveRooms()
    limits = RateLimits()

    @asynccontextmanager
    async def lifespan(app):
        stop = Event()

        def advance():
            while not stop.wait(0.1):
                try:
                    rooms.tick()
                except Exception:
                    # Do not continue a table after an unexpected partial tick.
                    app.state.runtime_failed = True
                    logging.getLogger(__name__).error("Game runtime stopped; restart the opening to reset")
                    return
        worker = Thread(target=advance, name="multiplayer-tick", daemon=True)
        worker.start()
        try:
            yield
        finally:
            stop.set()
            await asyncio.to_thread(worker.join)
            close = getattr(rooms, "close", None)
            if callable(close):
                await run_in_threadpool(close)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.rooms = rooms
    app.state.runtime_failed = False
    app.state.pending_sockets = 0
    app.add_middleware(IngressMiddleware, settings=settings)

    def healthy():
        if app.state.runtime_failed:
            raise HTTPException(503, "游戏运行已暂停，请管理员重新开服")

    def token_from(request):
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer ") or not 1 <= len(authorization) <= 256:
            raise HTTPException(401, "身份已失效，请重新进入")
        token = authorization[7:]
        with rooms.lock:
            try:
                rooms.client(token)
            except RoomError:
                raise HTTPException(401, "服务器已重新开服或身份已失效") from None
        return token

    async def body_from(request):
        try:
            body = json.loads(await request.body(), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
            if type(body) is not dict:
                raise ValueError()
            return body
        except (ValueError, UnicodeError, RecursionError):
            raise HTTPException(400, "请提交有效 JSON 对象") from None

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    @app.get("/healthz")
    def health():
        healthy()
        return {"ok": True, "server_instance_id": rooms.instance_id}

    @app.post("/api/local/session")
    async def session(request: Request):
        healthy()
        await body_from(request)
        if not limits.take("sessions", rate=1, burst=12):
            raise HTTPException(429, "创建身份过快，请稍后再试")
        try:
            token = await run_in_threadpool(rooms.new_client)
        except RoomError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"token": token, "server_instance_id": rooms.instance_id}

    @app.get("/api/local/state")
    def state(request: Request):
        healthy()
        token = token_from(request)
        with rooms.lock:
            rooms.tick()
            return rooms.view(token)

    @app.post("/api/local/action")
    async def action(request: Request):
        healthy()
        token = token_from(request)
        command = await body_from(request)
        if command.get("type") in {"create", "join"}:
            # Bound costly password hashing separately from cheap state reads.
            if not limits.take("passwords", rate=3, burst=8) or not limits.take((token, "password"), rate=0.5, burst=3):
                raise HTTPException(429, "尝试房间过快，请稍后再试")
        try:
            result = await run_in_threadpool(rooms.dispatch, token, command)
            return {"state": result}
        except (ValueError, KeyError, TypeError) as exc:
            message = str(exc) if isinstance(exc, ValueError) else "操作参数不正确"
            return JSONResponse({"error": message, "state": rooms.view(token)}, status_code=400)

    @app.get("/api/local/wall-record")
    def wall(request: Request):
        healthy()
        token = token_from(request)
        query = request.query_params
        if set(query) != {"room_id", "round_id"} or any(len(query.getlist(key)) != 1 for key in query):
            raise HTTPException(400, "请指定当前房间和结算局")
        try:
            with rooms.lock:
                rooms.tick()
                return {"wall_record": rooms.wall_record(token, room_id=query["room_id"], round_id=query["round_id"])}
        except RoomError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.websocket("/api/local/presence")
    async def presence(socket: WebSocket):
        if app.state.pending_sockets >= 32 or app.state.runtime_failed:
            await socket.close(code=1013)
            return
        app.state.pending_sockets += 1
        token, connection = None, None
        pending = True
        try:
            await socket.accept()
            raw = await asyncio.wait_for(socket.receive_text(), timeout=5)
            if len(raw) > 512:
                raise ValueError("Oversized handshake")
            hello = json.loads(raw)
            if type(hello) is not dict or set(hello) != {"token", "server_instance_id"} or type(hello["token"]) is not str or len(hello["token"]) > 128:
                raise ValueError("Invalid handshake")
            token = hello["token"]
            connection = await run_in_threadpool(rooms.open_connection, token, instance_id=hello["server_instance_id"])
            app.state.pending_sockets -= 1
            pending = False
            await socket.send_json({"ready": True, "connection_id": connection, "server_instance_id": rooms.instance_id})
            # Transport ping/pong is handled by Uvicorn, including background tabs.
            # Application data never carries game commands over this channel.
            while True:
                raw = await socket.receive_text()
                if raw != "ping" or not limits.take((connection, "ws"), rate=0.5, burst=3):
                    await socket.close(code=1008)
                    return
                await socket.send_text("pong")
        except (ValueError, KeyError, TypeError, RoomError, TimeoutError):
            await socket.close(code=4001)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            if pending:
                app.state.pending_sockets -= 1
            if connection is not None:
                await run_in_threadpool(rooms.close_connection, token, connection)

    @app.get("/")
    @app.get("/index.html")
    def index():
        html = (SHARED_STATIC / "index.html").read_text(encoding="utf-8")
        html = html.replace('<script src="/app.js" defer></script>', '<link rel="stylesheet" href="/multiplayer.css"><script src="/multiplayer.js" defer></script><script src="/app.js" defer></script>')
        html = html.replace('<body>', '<body class="local-multiplayer">')
        html = html.replace('</header>', '</header>' + (LOCAL_STATIC / "controls.html").read_text(encoding="utf-8"), 1)
        return HTMLResponse(html)

    @app.get("/{path:path}")
    def asset(path: str):
        paths = {"app.js": SHARED_STATIC / "app.js", "styles.css": SHARED_STATIC / "styles.css",
                 "multiplayer.js": LOCAL_STATIC / "client.js", "multiplayer.css": LOCAL_STATIC / "local.css"}
        if path.startswith("tiles/") and path[6:] in TILE_NAMES:
            return FileResponse(PICTURES / path[6:])
        if path.startswith("assets/") and path[7:] in ASSETS:
            return FileResponse(SHARED_STATIC / "assets" / path[7:])
        if path in paths:
            return FileResponse(paths[path])
        raise HTTPException(404, "接口或资源不存在")

    return app


def main():
    parser = argparse.ArgumentParser(description="开服即新局；Ctrl+C 关服，下次不恢复。Access 尚未接入，仅本机测试。")
    parser.add_argument("--port", type=int, default=8771)
    parser.add_argument("--public-origin", help="预留的精确 HTTPS 来源；配置不代表已接入 Access，勿提前发布 Tunnel")
    parser.add_argument("--disconnect-seconds", type=float, default=10, help="失去最后一个连接后的交接容错，默认 10 秒")
    parser.add_argument("--grace-seconds", type=float, default=300, help="每席每局累计宽限，默认 300 秒；缩短仅供隔离验收")
    args = parser.parse_args()
    origins = [f"http://127.0.0.1:{args.port}", f"http://localhost:{args.port}"]
    if args.public_origin:
        if not args.public_origin.startswith("https://"):
            parser.error("公开来源必须是 HTTPS")
        origins.append(args.public_origin)
    try:
        app = create_app(settings=WebSettings(tuple(origins)), runtime=LiveRooms(disconnect_seconds=args.disconnect_seconds, grace_seconds=args.grace_seconds))
    except ValueError as exc:
        parser.error(str(exc))
    print(f"Opening: http://127.0.0.1:{args.port}", flush=True)
    print("Memory only; reset on every process start. Access NOT configured; do not publish a tunnel yet.", flush=True)
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port, workers=1, reload=False, proxy_headers=False,
                access_log=False, server_header=False, ws="websockets", ws_max_size=4096, ws_max_queue=4,
                ws_ping_interval=5, ws_ping_timeout=10, ws_per_message_deflate=False,
                limit_concurrency=512, timeout_keep_alive=5, timeout_graceful_shutdown=3)


if __name__ == "__main__":
    main()
