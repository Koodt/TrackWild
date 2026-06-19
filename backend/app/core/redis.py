"""Redis connection pool for TrackWild."""

import redis.asyncio as aioredis

from app.core.config import settings

_pool: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    """Get or create a singleton Redis connection."""
    global _pool
    if _pool is None:
        _pool = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_timeout=30,
            socket_connect_timeout=5,
        )
    return _pool


async def close_redis() -> None:
    """Close the Redis connection pool."""
    global _pool
    if _pool:
        await _pool.aclose()
        _pool = None
