"""Application factory. One API process supervises multiple document processes."""
from contextlib import asynccontextmanager
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import FileResponse, JSONResponse
from starlette.staticfiles import StaticFiles

from .config import Settings
from .middleware import BodyLimitMiddleware
from .rate_limit import RateLimiter
from .routes import router
from .runner import ProcessingError
from .security import Sessions
from .service import Service

LOG = logging.getLogger(__name__)


def create_app(settings=None):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(application):
        service = Service(settings)
        with service.storage.instance_lock():
            service.storage.recover()
            application.state.settings = settings
            application.state.service = service
            application.state.sessions = Sessions(settings.secret or service.storage.local_secret())
            application.state.rate_limiter = RateLimiter(settings)
            try:
                yield
            finally:
                await service.runner.close()

    application = FastAPI(title="API проверки технической документации", version="0.1.0",
                          lifespan=lifespan, docs_url=None, redoc_url=None,
                          description="Формальная проверка и исправление DOCX без базы данных. Разные документы обрабатываются параллельно.")
    application.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))
    application.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_upload_bytes + 1024 * 1024,
                              upload_timeout=settings.upload_timeout, idle_timeout=settings.upload_idle_timeout)
    application.include_router(router)

    # Only the dedicated UI directory is public; document storage stays private.
    frontend = Path(__file__).resolve().parent.parent / "frontend"
    application.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")

    @application.get("/", include_in_schema=False)
    async def index():
        return FileResponse(frontend / "index.html", headers={"Cache-Control": "no-cache"})

    @application.exception_handler(ProcessingError)
    async def processing_error(request: Request, exc: ProcessingError):
        retry_after = exc.retry_after if exc.retry_after is not None else (5 if exc.status_code == 503 else None)
        headers = {"Retry-After": str(retry_after)} if retry_after is not None else None
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status_code, headers=headers)

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return JSONResponse({"error": {"code": "request_error", "message": str(exc.detail)}}, status_code=exc.status_code)

    @application.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        LOG.exception("Unexpected API error")
        return JSONResponse({"error": {"code": "internal_error", "message": "Внутренняя ошибка сервера. Повторите попытку позже."}}, status_code=500)

    return application


app = create_app()
