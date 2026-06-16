import asyncio
import time
from collections import defaultdict
from typing import Any

from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import Receive, Scope, Send


class RateLimiter:
    """Simple sliding-window rate limiter."""

    def __init__(self, max_requests: int = 60, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests: dict[str, list[float]] = defaultdict(list)
        self._cleanup_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop(self) -> None:
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(self.window_seconds * 2)
            now = time.monotonic()
            cutoff = now - self.window_seconds
            to_delete = []
            for ip, timestamps in self.requests.items():
                self.requests[ip] = [t for t in timestamps if t > cutoff]
                if not self.requests[ip]:
                    to_delete.append(ip)
            for ip in to_delete:
                del self.requests[ip]

    def is_allowed(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        self.requests[key] = [
            t for t in self.requests.get(key, []) if t > cutoff
        ]
        if len(self.requests[key]) >= self.max_requests:
            return False
        self.requests[key].append(now)
        return True


tile_rate_limiter = RateLimiter(max_requests=120, window_seconds=60)


class TileRateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limit tile requests to prevent abuse."""

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        if not request.url.path.startswith("/v1/tiles/"):
            return await call_next(request)

        client_ip = request.headers.get(
            "x-forwarded-for", request.client.host if request.client else "unknown"
        ).split(",")[0].strip()

        if not tile_rate_limiter.is_allowed(client_ip):
            raise HTTPException(
                status_code=429,
                detail="Too many requests",
                headers={"Retry-After": "60"},
            )

        return await call_next(request)
