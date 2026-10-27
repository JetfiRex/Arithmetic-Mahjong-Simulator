"""Bounded HTTP/WS ingress for a single-process, loopback-only ASGI server."""

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import time
from urllib.parse import urlsplit

from starlette.responses import JSONResponse


MAX_BODY = 65536


@dataclass(frozen=True)
class WebSettings:
    origins: tuple[str, ...]

    def __post_init__(self):
        if not self.origins:
            raise ValueError("At least one exact origin is required")
        for origin in self.origins:
            parts = urlsplit(origin)
            if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password
                    or parts.path or parts.query or parts.fragment or origin != f"{parts.scheme}://{parts.netloc}"
                    or "*" in origin or parts.netloc != parts.netloc.lower()):
                raise ValueError("Use an exact origin without path, credentials or wildcard")
            if parts.scheme == "http" and parts.hostname not in {"127.0.0.1", "localhost", "::1"}:
                raise ValueError("Remote origins require HTTPS")

    def permits(self, headers, *, write=False):
        hosts = [v.decode("latin1") for k, v in headers if k.lower() == b"host"]
        origins = [v.decode("latin1") for k, v in headers if k.lower() == b"origin"]
        if len(hosts) != 1 or len(origins) > 1:
            return False
        host = hosts[0].lower()
        matching = {o for o in self.origins if urlsplit(o).netloc == host}
        if not matching:
            return False
        if not origins:
            return not write
        return origins[0] in matching


class RateLimits:
    def __init__(self):
        self.buckets = OrderedDict()

    def take(self, key, *, rate, burst, now=None):
        timestamp = time.monotonic() if now is None else now
        amount, last = self.buckets.pop(key, (burst, timestamp))
        amount = min(burst, amount + max(0, timestamp - last) * rate)
        allowed = amount >= 1
        self.buckets[key] = (amount - 1 if allowed else amount, timestamp)
        while len(self.buckets) > 4096:
            self.buckets.popitem(last=False)
        return allowed


class IngressMiddleware:
    """Do not trust forwarded IP, host, scheme or Cloudflare identity headers."""

    def __init__(self, app, *, settings):
        self.app, self.settings, self.limits = app, settings, RateLimits()

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        websocket = scope["type"] == "websocket"
        method = scope.get("method", "GET")
        write = websocket or method not in {"GET", "HEAD"}

        async def fail(status, message):
            if websocket:
                await send({"type": "websocket.close", "code": 1008})
            else:
                await JSONResponse({"error": message}, status_code=status,
                                   headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})(scope, receive, send)

        if not self.settings.permits(scope["headers"], write=write):
            return await fail(403, "域名或来源不被允许")
        headers = dict(scope["headers"])
        # Opening the public entry page from an external link is a normal
        # top-level navigation. API requests, frames and writes stay protected.
        entry_navigation = (
            not websocket and method == "GET" and scope.get("path") in {"/", "/index.html"}
            and headers.get(b"sec-fetch-mode") == b"navigate"
            and headers.get(b"sec-fetch-dest") == b"document"
        )
        if headers.get(b"sec-fetch-site") == b"cross-site" and not entry_navigation:
            return await fail(403, "不允许跨站请求")
        if not self.limits.take("global", rate=400, burst=800):
            return await fail(429, "请求过多，请稍后重试")
        if write:
            key = hashlib.sha256(headers.get(b"authorization", b"anonymous")).hexdigest()
            if not self.limits.take((key, "write"), rate=10, burst=30):
                return await fail(429, "操作过快，请稍后重试")
        if not websocket and method == "POST":
            if headers.get(b"content-type", b"").split(b";", 1)[0].strip() != b"application/json":
                return await fail(415, "请提交 JSON")
            lengths = [v for k, v in scope["headers"] if k == b"content-length"]
            try:
                if len(lengths) > 1 or (lengths and not 0 < int(lengths[0]) <= MAX_BODY):
                    return await fail(413, "请求内容过大或长度无效")
            except ValueError:
                return await fail(400, "请求长度无效")
            body = bytearray()
            try:
                while True:
                    message = await asyncio.wait_for(receive(), timeout=5)
                    if message["type"] == "http.disconnect":
                        return
                    body.extend(message.get("body", b""))
                    if len(body) > MAX_BODY:
                        return await fail(413, "请求内容过大")
                    if not message.get("more_body", False):
                        break
            except TimeoutError:
                return await fail(408, "请求读取超时")
            delivered = False

            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()
            body_receive = replay
        else:
            body_receive = receive

        async def secured_send(message):
            if message["type"] == "http.response.start":
                extras = {
                    "cache-control": "no-store", "x-content-type-options": "nosniff",
                    "referrer-policy": "no-referrer", "x-frame-options": "DENY",
                    "permissions-policy": "camera=(), microphone=(), geolocation=()",
                    "content-security-policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                    "img-src 'self' data:; connect-src 'self' " + " ".join(o.replace("https://", "wss://").replace("http://", "ws://") for o in self.settings.origins)
                    + "; frame-ancestors 'none'; object-src 'none'; base-uri 'none'; form-action 'self'",
                }
                current = [(k, v) for k, v in message.get("headers", []) if k.decode().lower() not in extras]
                message = {**message, "headers": current + [(k.encode(), v.encode()) for k, v in extras.items()]}
            await send(message)
        await self.app(scope, body_receive, secured_send)
