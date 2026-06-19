"""Redis-based tile queue producer for TrackWild.

Provides atomic enqueue with deduplication via Lua scripting,
plus status/query helpers for the API layer.
"""

import logging

import redis.asyncio as aioredis

from app.core.redis import get_redis

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Key constants
# ---------------------------------------------------------------------------
DEDUP_SET_KEY = "tw:tile:pending"
ONDEMAND_STREAM = "tw:tile:stream:ondemand"
PREGEN_STREAM = "tw:tile:stream:pregen"
DEDUP_TTL = 3600  # seconds
STREAM_MAXLEN = 100000

# ---------------------------------------------------------------------------
# Lua script: atomic SISMEMBER + SADD + XADD
# KEYS[1] = dedup set
# KEYS[2] = stream
# ARGV[1] = dedup key ("time_slot:z:x:y")
# ARGV[2] = time_slot
# ARGV[3] = z
# ARGV[4] = x
# ARGV[5] = y
# ARGV[6] = ttl (seconds)
# ---------------------------------------------------------------------------
_ENGueue_LUA = """
if redis.call('SISMEMBER', KEYS[1], ARGV[1]) == 1 then
    return 'duplicate'
end
redis.call('SADD', KEYS[1], ARGV[1])
redis.call('EXPIRE', KEYS[1], ARGV[6])
redis.call('XADD', KEYS[2], 'MAXLEN', '~', ARGV[7], '*',
           'time_slot', ARGV[2], 'z', ARGV[3], 'x', ARGV[4], 'y', ARGV[5])
return 'ok'
"""


class TileQueue:
    """Producer API for the tile generation Redis Streams queue."""

    def __init__(self) -> None:
        self._script_sha: str | None = None

    async def _get_script(self, r: aioredis.Redis) -> str:
        """Register the Lua script and cache its SHA."""
        if self._script_sha is None:
            self._script_sha = await r.script_load(_ENGueue_LUA)
        assert self._script_sha is not None
        return self._script_sha

    async def enqueue(
        self,
        time_slot: str,
        z: int,
        x: int,
        y: int,
        priority: str = "ondemand",
    ) -> str:
        """Atomically deduplicate and enqueue a tile generation task.

        Returns:
            "ok" if enqueued, "duplicate" if already pending.
        """
        r = await get_redis()
        sha = await self._get_script(r)
        dedup_key = f"{time_slot}:{z}:{x}:{y}"
        stream = ONDEMAND_STREAM if priority == "ondemand" else PREGEN_STREAM
        result = await r.evalsha(
            sha,
            2,
            DEDUP_SET_KEY,
            stream,
            dedup_key,
            time_slot,
            str(z),
            str(x),
            str(y),
            str(DEDUP_TTL),
            str(STREAM_MAXLEN),
        )
        return result  # type: ignore[return-value]

    async def queue_size(self) -> dict:
        """Return queue statistics.

        Returns:
            Dict with ondemand_queued, ondemand_inflight,
            pregen_queued, pregen_inflight, pending_dedup_set.
        """
        r = await get_redis()
        ondemand_len = await r.xlen(ONDEMAND_STREAM)
        pregen_len = await r.xlen(PREGEN_STREAM)
        dedup_len = await r.scard(DEDUP_SET_KEY)

        # Inflight = pending in dedup set but not yet in any stream
        # (approximate: dedup set size minus sum of stream lengths)
        queued_total = ondemand_len + pregen_len
        inflight_approx = max(0, dedup_len - queued_total)

        # Split inflight between ondemand and pregen proportionally
        if queued_total > 0:
            ondemand_inflight = int(inflight_approx * ondemand_len / queued_total)
            pregen_inflight = inflight_approx - ondemand_inflight
        else:
            ondemand_inflight = 0
            pregen_inflight = 0

        return {
            "ondemand_queued": ondemand_len,
            "ondemand_inflight": ondemand_inflight,
            "pregen_queued": pregen_len,
            "pregen_inflight": pregen_inflight,
            "pending_dedup_set": dedup_len,
        }

    async def is_pending(self, time_slot: str, z: int, x: int, y: int) -> bool:
        """Check if a tile is currently pending (in dedup set)."""
        r = await get_redis()
        dedup_key = f"{time_slot}:{z}:{x}:{y}"
        return bool(await r.sismember(DEDUP_SET_KEY, dedup_key))

    async def get_layer_remaining(self, z: int) -> int | None:
        """Get remaining tile count for a pregen layer.

        Returns:
            Remaining count or None if key doesn't exist.
        """
        r = await get_redis()
        layer_key = f"tw:tile:pregen:layer:{z}"
        val = await r.get(layer_key)
        if val is None:
            return None
        return int(val)

    async def set_layer_total(self, z: int, total: int) -> None:
        """Set the total tile count for a pregen layer counter."""
        r = await get_redis()
        layer_key = f"tw:tile:pregen:layer:{z}"
        await r.set(layer_key, total)

    async def decr_layer(self, z: int, by: int = 1) -> None:
        """Decrement the pregen layer counter."""
        r = await get_redis()
        layer_key = f"tw:tile:pregen:layer:{z}"
        await r.decrby(layer_key, by)

    async def remove_dedup(self, time_slot: str, z: int, x: int, y: int) -> None:
        """Remove a tile from the dedup set (after processing)."""
        r = await get_redis()
        dedup_key = f"{time_slot}:{z}:{x}:{y}"
        await r.srem(DEDUP_SET_KEY, dedup_key)


# Singleton instance
tile_queue = TileQueue()
