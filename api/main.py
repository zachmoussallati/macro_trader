"""FastAPI application entry point."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from api.auth.routes import router as auth_router
from api.routers.calendar import router as calendar_router
from api.routers.data import router as data_router
from api.routers.health import router as health_router
from api.routers.methods import router as methods_router
from api.routers.regime import router as regime_router
from api.routers.signals import router as signals_router
from macro_trader import __version__
from macro_trader.config import get_settings
from macro_trader.logging_setup import configure_logging, get_logger

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(_app: FastAPI):  # type: ignore[no-untyped-def]
    configure_logging()
    log = get_logger("api.startup")
    settings = get_settings()
    log.info(
        "macro_trader.api.startup",
        env=settings.env,
        version=__version__,
        api_port=settings.api.port,
    )
    yield
    log.info("macro_trader.api.shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Macro Trader API",
        description="Systematic macro trading system — Stage 1 foundation.",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url=f"{API_PREFIX}/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.api.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            path=request.url.path,
            method=request.method,
        )
        try:
            response = await call_next(request)
            response.headers["x-request-id"] = request_id
            return response
        finally:
            structlog.contextvars.clear_contextvars()

    app.include_router(health_router, prefix=API_PREFIX)
    app.include_router(auth_router, prefix=API_PREFIX)
    app.include_router(methods_router, prefix=API_PREFIX)
    app.include_router(data_router, prefix=API_PREFIX)
    app.include_router(calendar_router, prefix=API_PREFIX)
    app.include_router(signals_router, prefix=API_PREFIX)
    app.include_router(regime_router, prefix=API_PREFIX)

    return app


app = create_app()
