"""Bound request bodies before multipart parsing starts consuming disk."""
import asyncio

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse


class BodyLimitMiddleware:
    def __init__(self, app, max_bytes):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        try:
            too_large = int(headers.get(b"content-length", b"0")) > self.max_bytes
        except ValueError:
            response = JSONResponse({"detail": "Некорректный размер запроса."}, status_code=400)
            return await response(scope, receive, send)
        if too_large:
            response = JSONResponse({"detail": "Файл превышает допустимый размер."}, status_code=413)
            return await response(scope, receive, send)
        total = 0

        async def limited_receive():
            nonlocal total
            try:
                message = await asyncio.wait_for(receive(), 30)
            except asyncio.TimeoutError:
                raise HTTPException(408, "Превышено время загрузки файла.") from None
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > self.max_bytes:
                    raise HTTPException(413, "Файл превышает допустимый размер.")
            return message

        async def secured_send(message):
            if message["type"] == "http.response.start":
                extra = [(b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"same-origin"),
                         (b"x-frame-options", b"DENY"),
                         (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")]
                if scope["path"].startswith("/api/"):
                    extra.append((b"cache-control", b"no-store"))
                message["headers"] = list(message.get("headers", [])) + extra
            await send(message)

        await self.app(scope, limited_receive, secured_send)
