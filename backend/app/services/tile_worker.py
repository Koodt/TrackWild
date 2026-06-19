"""Background tile generation worker using Redis Streams for TrackWild."""

import asyncio
import logging
import math
import uuid
from pathlib import Path
from typing import Optional

from app.core.config import settings
from app.core.database import async_session_factory
from app.core.redis import get_redis
from app.services.tile_generator import generate_osm_tile_png
from app.services.tile_queue import (
    DEDUP_SET_KEY,
    ONDEMAND_STREAM,
    PREGEN_STREAM,
    TileQueue,
    tile_queue,
)
from app.services.tile_service import find_stale_tiles, save_tile

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Worker config
# ---------------------------------------------------------------------------
_CONSUMER_GROUP = "tile-workers"
_CONSUMER_NAME = f"worker-{uuid.uuid4().hex[:8]}"
_SEMAPHORE = asyncio.Semaphore(settings.tile_workers)
_shutdown_event: asyncio.Event = asyncio.Event()

# Background tasks
_consume_task: Optional[asyncio.Task] = None
_pregen_task: Optional[asyncio.Task] = None
_stale_task: Optional[asyncio.Task] = None

# ---------------------------------------------------------------------------
# Tile processing
# ---------------------------------------------------------------------------

async def _process(time_slot: str, z: int, x: int, y: int) -> None:
    """Generate a single tile and save to cache + DB."""
    try:
        async with async_session_factory() as session:
            png_data = await generate_osm_tile_png(z, x, y, time_slot, session)

        # Skip empty tiles — they waste space and get needlessly re-queued.
        if png_data is None:
            logger.debug("Tile %s/%d/%d/%d is empty, skipping", time_slot, z, x, y)
            return

        cache_path = (
            Path(settings.tile_cache_dir)
            / time_slot
            / str(z)
            / str(x)
            / f"{y}.png"
        )
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(png_data)

        await save_tile(time_slot, z, x, y, png_data)

        logger.info("Generated tile %s/%d/%d/%d", time_slot, z, x, y)
    except Exception:
        logger.exception("Tile generation failed for %s/%d/%d/%d", time_slot, z, x, y)


# ---------------------------------------------------------------------------
# Consumer loop
# ---------------------------------------------------------------------------

async def _consume_loop() -> None:
    """Read from Redis Streams and process tiles.

    Priority: ondemand first, then pregen if ondemand is empty.
    Uses XREADGROUP with BLOCK for efficient waiting.
    """
    r = await get_redis()
    await _ensure_consumer_groups(r)

    while not _shutdown_event.is_set():
        try:
            # Try ondemand first
            messages = await _read_from_stream(
                r, ONDEMAND_STREAM, _CONSUMER_GROUP, _CONSUMER_NAME,
            )
            if not messages:
                # Fall back to pregen
                messages = await _read_from_stream(
                    r, PREGEN_STREAM, _CONSUMER_GROUP, _CONSUMER_NAME,
                )

            if not messages:
                continue

            for stream_name, msg_id, fields in messages:
                time_slot = fields.get("time_slot", "")
                z = int(fields.get("z", 0))
                x = int(fields.get("x", 0))
                y = int(fields.get("y", 0))
                is_pregen = stream_name == PREGEN_STREAM

                await _SEMAPHORE.acquire()
                asyncio.create_task(
                    _worker_task(time_slot, z, x, y, msg_id, is_pregen),
                )
        except asyncio.CancelledError:
            break
        except TimeoutError:
            continue
        except Exception:
            logger.exception("Error in consume loop")
            await asyncio.sleep(1)


async def _worker_task(
    time_slot: str, z: int, x: int, y: int,
    msg_id: str, is_pregen: bool,
) -> None:
    """Process a tile and ACK it in the stream."""
    try:
        await _process(time_slot, z, x, y)
    finally:
        r = await get_redis()
        # Always ACK the message
        await r.xack(ONDEMAND_STREAM if not is_pregen else PREGEN_STREAM,
                      _CONSUMER_GROUP, msg_id)
        # Remove from dedup set
        await tile_queue.remove_dedup(time_slot, z, x, y)
        # For pregen: decrement layer counter (always, even if tile was empty)
        if is_pregen:
            await tile_queue.decr_layer(z)
        _SEMAPHORE.release()


async def _read_from_stream(
    r,  # aioredis.Redis
    stream: str,
    group: str,
    consumer: str,
    block_ms: int = 5000,
) -> list:
    """Read pending/new messages from a stream via XREADGROUP."""
    try:
        # '>' means only new (undelivered) messages
        resp = await r.xreadgroup(
            group, consumer, {stream: ">"}, count=10, block=block_ms,
        )
        if not resp:
            return []
        # resp: [(stream_name, [(msg_id, fields), ...])]
        result = []
        for stream_name, msg_list in resp:
            for msg_id, fields in msg_list:
                result.append((stream_name.decode() if isinstance(stream_name, bytes) else stream_name,
                               msg_id, fields))
        return result
    except Exception as exc:
        # No such group or no messages — not an error
        if "NOGROUP" in str(exc) or "nil" in str(exc).lower():
            return []
        raise


async def _ensure_consumer_groups(r) -> None:  # aioredis.Redis
    """Create consumer groups if they don't exist."""
    for stream in (ONDEMAND_STREAM, PREGEN_STREAM):
        try:
            await r.xgroup_create(stream, _CONSUMER_GROUP, id="0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" in str(exc):
                pass  # group already exists
            else:
                logger.warning("Could not create consumer group for %s: %s", stream, exc)


# ---------------------------------------------------------------------------
# Pre-generation with layer counters
# ---------------------------------------------------------------------------

# Time slots that need pre-generation
TIME_SLOTS = ["night", "morning", "day", "evening"]


def _latlon_to_tile(lat_deg: float, lon_deg: float, zoom: int) -> tuple[int, int]:
    """Convert lat/lon (EPSG:4326) to tile x/y at given zoom."""
    lat_rad = math.radians(lat_deg)
    n = 2.0 ** zoom
    xtile = int((lon_deg + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return xtile, ytile


def _tiles_for_bbox(zoom: int) -> list[tuple[int, int]]:
    """Return all (x, y) tile coords inside the configured pregen_bbox at given zoom.

    Bbox format: "min_lon,min_lat,max_lon,max_lat" in EPSG:4326.
    Note: tile y increases south, so we invert lat ordering.
    """
    parts = [float(p.strip()) for p in settings.pregen_bbox.split(",")]
    if len(parts) != 4:
        logger.error("Invalid pregen_bbox: %s", settings.pregen_bbox)
        return []
    min_lon, min_lat, max_lon, max_lat = parts

    # Top-left (northwest) → smaller tile y
    x_min, y_north = _latlon_to_tile(max_lat, min_lon, zoom)
    # Bottom-right (southeast) → larger tile y
    x_max, y_south = _latlon_to_tile(min_lat, max_lon, zoom)

    tiles: list[tuple[int, int]] = []
    for x in range(x_min, x_max + 1):
        for y in range(y_north, y_south + 1):
            tiles.append((x, y))
    return tiles


async def _pre_generate() -> None:
    """Pre-generate tiles layer by layer (z_min → z_max), waiting for each layer."""
    if not settings.pregen_enabled:
        logger.info("Pre-generation disabled")
        return
    if _shutdown_event.is_set():
        return

    r = await get_redis()

    for z in range(settings.pregen_z_min, settings.pregen_z_max + 1):
        if _shutdown_event.is_set():
            logger.info("Pre-generation interrupted at z=%d", z)
            break

        tiles = _tiles_for_bbox(z)
        if not tiles:
            continue

        layer_total = len(tiles) * len(TIME_SLOTS)
        logger.info(
            "Pre-gen layer z=%d: enqueuing %d tiles (%d coords × %d slots)",
            z, layer_total, len(tiles), len(TIME_SLOTS),
        )

        # Set layer counter
        await tile_queue.set_layer_total(z, layer_total)

        for time_slot in TIME_SLOTS:
            for x, y in tiles:
                await tile_queue.enqueue(time_slot, z, x, y, priority="pregen")

        # Wait for this entire layer to finish via counter polling
        while True:
            if _shutdown_event.is_set():
                logger.info("Pre-generation interrupted waiting for z=%d", z)
                break
            remaining = await tile_queue.get_layer_remaining(z)
            if remaining is not None and remaining <= 0:
                break
            await asyncio.sleep(0.5)
        else:
            continue

        logger.info("Pre-gen layer z=%d: done", z)

    logger.info("Pre-generation complete (z=%d..%d)", settings.pregen_z_min, settings.pregen_z_max)


# ---------------------------------------------------------------------------
# Stale checker
# ---------------------------------------------------------------------------

async def _stale_checker() -> None:
    """Periodically check for stale tiles (older than TTL) and re-queue them."""
    while not _shutdown_event.is_set():
        try:
            stale = await find_stale_tiles(
                ttl_hours=settings.pregen_ttl_hours,
                limit=settings.pregen_stale_batch,
            )
            if stale:
                for time_slot, z, x, y in stale:
                    await tile_queue.enqueue(time_slot, z, x, y)
                logger.info("Stale check: re-queued %d tiles (TTL=%d hours)",
                            len(stale), settings.pregen_ttl_hours)
        except Exception:
            logger.exception("Stale check failed")

        # Sleep in small increments so we can respond to shutdown quickly
        for _ in range(settings.pregen_stale_check_seconds * 2):
            if _shutdown_event.is_set():
                break
            await asyncio.sleep(0.5)


# ---------------------------------------------------------------------------
# Public lifecycle API
# ---------------------------------------------------------------------------

def start_worker() -> None:
    """Start the background consumer + pre-generator + stale checker tasks."""
    global _consume_task, _pregen_task, _stale_task
    _shutdown_event.clear()

    if _consume_task is None or _consume_task.done():
        _consume_task = asyncio.create_task(_consume_loop())
        logger.info("Tile consumer started (consumer=%s)", _CONSUMER_NAME)
    if settings.pregen_enabled and (_pregen_task is None or _pregen_task.done()):
        _pregen_task = asyncio.create_task(_pre_generate())
        logger.info("Pre-generator started")
    if _stale_task is None or _stale_task.done():
        _stale_task = asyncio.create_task(_stale_checker())
        logger.info("Stale tile checker started (TTL=%d hours)", settings.pregen_ttl_hours)


async def stop_worker() -> None:
    """Cancel all background tasks gracefully."""
    global _consume_task, _pregen_task, _stale_task
    _shutdown_event.set()

    for task in (_consume_task, _pregen_task, _stale_task):
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    _consume_task = None
    _pregen_task = None
    _stale_task = None
    logger.info("Tile worker stopped")
