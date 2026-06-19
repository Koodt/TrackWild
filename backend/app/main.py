from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from app.api.v1.router import router as v1_router
from app.core.config import settings
from app.core.database import engine, on_startup as db_on_startup
from app.core.rate_limit import TileRateLimitMiddleware, tile_rate_limiter
from app.core.redis import close_redis
from app.services.tile_worker import start_worker, stop_worker


@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:
    """Application lifespan events."""
    await db_on_startup()
    await tile_rate_limiter.start()
    start_worker()
    yield
    await stop_worker()
    await tile_rate_limiter.stop()
    await close_redis()
    await engine.dispose()


def is_production() -> bool:
    """Check if running in production environment."""
    return settings.env == "production"


app = FastAPI(
    title="TrackWild API",
    description="Wildlife encounter risk map backend",
    version="0.1.0",
    lifespan=lifespan,
    openapi_url=None if is_production() else "/openapi.json",
    docs_url=None if is_production() else "/docs",
    redoc_url=None if is_production() else "/redoc",
)

# Rate limiting for tile endpoints
app.add_middleware(TileRateLimitMiddleware)

# CORS — only allow our domain
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# Security headers middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next: Any) -> Response:
    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if "Server" in response.headers:
        del response.headers["Server"]
    return response


# Include public routers (only tiles)
app.include_router(v1_router)


# Internal health check — NOT proxied by Caddy, used only by docker healthcheck
@app.get("/healthz")
async def healthz() -> JSONResponse:
    return JSONResponse(content={"status": "ok"})


@app.get("/")
async def root() -> JSONResponse:
    return JSONResponse(content={"status": "ok"})
