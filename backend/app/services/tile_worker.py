"""Background tile generation worker for FastAPI."""

import asyncio
import logging
from pathlib import Path
from typing import Optional

from app.core.config import settings
from app.core.database import async_session_factory
from app.services.tile_generator import _TRANSPARENT_PNG, generate_osm_tile_png
from app.services.tile_service import find_stale_tiles, save_tile

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Worker state
# ---------------------------------------------------------------------------
_queue: asyncio.Queue[tuple[str, int, int, int]] = asyncio.Queue()
_semaphore = asyncio.Semaphore(settings.tile_workers)
_pending: set[tuple[str, int, int, int]] = set()
_consume_task: Optional[asyncio.Task] = None
_pregen_task: Optional[asyncio.Task] = None
_stale_task: Optional[asyncio.Task] = None


async def _process(time_slot: str, z: int, x: int, y: int) -> None:
    key = (time_slot, z, x, y)
    try:
        async with async_session_factory() as session:
            png_data = await generate_osm_tile_png(z, x, y, time_slot, session)

        if png_data is None:
            png_data = _TRANSPARENT_PNG

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
    finally:
        _pending.discard(key)


async def _consume() -> None:
    while True:
        time_slot, z, x, y = await _queue.get()
        try:
            async with _semaphore:
                await _process(time_slot, z, x, y)
        finally:
            _queue.task_done()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def start_worker() -> None:
    """Start the background consumer + pre-generator + stale checker tasks."""
    global _consume_task, _pregen_task, _stale_task
    if _consume_task is None or _consume_task.done():
        _consume_task = asyncio.create_task(_consume())
        logger.info("Tile consumer started")
    if settings.pregen_enabled:
        _pregen_task = asyncio.create_task(_pre_generate())
        logger.info("Pre-generator started")
    _stale_task = asyncio.create_task(_stale_checker())
    logger.info("Stale tile checker started (TTL=%d hours)", settings.pregen_ttl_hours)


async def stop_worker() -> None:
    """Cancel all background tasks gracefully."""
    global _consume_task, _pregen_task, _stale_task
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


def enqueue(time_slot: str, z: int, x: int, y: int) -> None:
    """Add a tile generation request to the queue (deduplicated)."""
    key = (time_slot, z, x, y)
    if key in _pending:
        return
    _pending.add(key)
    _queue.put_nowait((time_slot, z, x, y))


def queue_size() -> int:
    """Return the current number of items in the queue."""
    return _queue.qsize()


# ---------------------------------------------------------------------------
# Pre-generation helpers
# ---------------------------------------------------------------------------
import math

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

    for z in range(settings.pregen_z_min, settings.pregen_z_max + 1):
        tiles = _tiles_for_bbox(z)
        if not tiles:
            continue

        layer_total = len(tiles) * len(TIME_SLOTS)
        logger.info(
            "Pre-gen layer z=%d: enqueuing %d tiles (%d coords × %d slots)",
            z, layer_total, len(tiles), len(TIME_SLOTS),
        )

        for time_slot in TIME_SLOTS:
            for x, y in tiles:
                enqueue(time_slot, z, x, y)

        # Wait for this entire layer to finish before moving to next z
        await _queue.join()
        logger.info("Pre-gen layer z=%d: done", z)

    logger.info("Pre-generation complete (z=%d..%d)", settings.pregen_z_min, settings.pregen_z_max)


async def _stale_checker() -> None:
    """Periodically check for stale tiles (older than TTL) and re-queue them."""
    while True:
        try:
            stale = await find_stale_tiles(
                ttl_hours=settings.pregen_ttl_hours,
                limit=settings.pregen_stale_batch,
            )
            if stale:
                for time_slot, z, x, y in stale:
                    enqueue(time_slot, z, x, y)
                logger.info("Stale check: re-queued %d tiles (TTL=%d hours)", len(stale), settings.pregen_ttl_hours)
        except Exception:
            logger.exception("Stale check failed")
        await asyncio.sleep(settings.pregen_stale_check_seconds)
