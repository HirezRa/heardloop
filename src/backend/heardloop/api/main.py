"""HTTP API. Run: uvicorn heardloop.api.main:app"""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text

from heardloop.api import (
    routes_auth,
    routes_calendar,
    routes_campaigns,
    routes_linkedin,
    routes_media,
    routes_metrics,
    routes_mfa,
    routes_notifications,
    routes_posts,
    routes_settings,
    routes_templates,
    routes_version,
    routes_workspaces,
)
from heardloop.config import get_settings
from heardloop.db import create_engine, create_session_factory
from heardloop.delivery import refresh_blocked_service_ips
from heardloop.keycheck import ensure_token_key
from heardloop.logging_setup import configure_logging
from heardloop.security.crypto import TokenProtector
from heardloop.version import app_version

log = logging.getLogger("heardloop.api")

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    # JSON only: nothing here may load or embed anything. Media and CSV routes may set their own values.
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
_DOCS_PATHS = ("/api/docs", "/api/openapi.json")  # Swagger UI (development only) needs its own scripts


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_engine(settings)
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    await ensure_token_key(app.state.session_factory, settings.token_key.get_secret_value())
    app.state.token_protector = TokenProtector(settings.token_key.get_secret_value())
    app.state.http = httpx.AsyncClient(follow_redirects=False)
    await refresh_blocked_service_ips()
    log.info("API started", extra={"environment": settings.environment})
    yield
    await app.state.http.aclose()
    await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    docs = settings.is_development
    app = FastAPI(
        title="Heardloop",
        version=app_version(),
        lifespan=lifespan,
        docs_url="/api/docs" if docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if docs else None,
    )

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        docs_page = settings.is_development and request.url.path.startswith(_DOCS_PATHS)
        for name, value in _SECURITY_HEADERS.items():
            if docs_page and name == "Content-Security-Policy":
                continue
            response.headers.setdefault(name, value)
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        """Never echo the submitted values (a mistyped password must not come back in the response)."""
        detail = [
            {
                "loc": list(err.get("loc", ())),
                "msg": str(err.get("msg", "")),
                "type": str(err.get("type", "")),
            }
            for err in exc.errors()
        ]
        return JSONResponse({"detail": detail}, status_code=422)

    @app.get("/api/v1/health")
    async def health(request: Request) -> JSONResponse:
        try:
            async with request.app.state.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception:
            log.exception("Health check: database unreachable")
            return JSONResponse({"status": "unhealthy", "database": "down"}, status_code=503)
        return JSONResponse({"status": "healthy", "database": "up"})

    @app.get("/api/v1/meta")
    async def meta() -> dict[str, str | None]:
        """Public: the web app reads `ui_language` before sign-in to pick language and text direction."""
        return {
            "name": "Heardloop",
            "version": app.version,
            "environment": settings.environment if settings.is_development else None,
            "ui_language": settings.ui_language,
        }

    app.include_router(routes_auth.router)
    app.include_router(routes_mfa.router)
    app.include_router(routes_workspaces.router)
    app.include_router(routes_settings.router)
    app.include_router(routes_linkedin.router)
    app.include_router(routes_posts.router)
    app.include_router(routes_templates.router)
    app.include_router(routes_templates.posts_router)
    app.include_router(routes_media.router)
    app.include_router(routes_campaigns.router)
    app.include_router(routes_calendar.router)
    app.include_router(routes_notifications.router)
    app.include_router(routes_metrics.router)
    app.include_router(routes_version.router)
    return app


app = create_app()
