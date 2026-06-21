import logging
import math
import shutil
import uuid
from pathlib import Path
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.core.config import settings
from app.models.tile import Tile

logger = logging.getLogger(__name__)


async def get_tile(
    time_slot: str,
    z: int,
    x: int,
    y: int,
    session: AsyncSession | None = None,
) -> Optional[bytes]:
    """Retrieve tile PNG data from database."""
    query = select(Tile.png_data).where(
        Tile.time_slot == time_slot,
        Tile.zoom == z,
        Tile.tile_x == x,
        Tile.tile_y == y,
    )

    if session is None:
        async with async_session_factory() as session:
            result = await session.execute(query)
            return result.scalar_one_or_none()

    result = await session.execute(query)
    return result.scalar_one_or_none()


async def save_tile(
    time_slot: str,
    z: int,
    x: int,
    y: int,
    png_data: bytes,
) -> None:
    """Insert or update a tile in the database (race-safe via ON CONFLICT)."""
    async with async_session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO tiles (id, time_slot, zoom, tile_x, tile_y, png_data, generated_at) "
                "VALUES (:id, :time_slot, :zoom, :tile_x, :tile_y, :png_data, now()) "
                "ON CONFLICT (time_slot, zoom, tile_x, tile_y) "
                "DO UPDATE SET png_data = EXCLUDED.png_data, generated_at = now()"
            ),
            {
                "id": str(uuid.uuid4()),
                "time_slot": time_slot,
                "zoom": z,
                "tile_x": x,
                "tile_y": y,
                "png_data": png_data,
            },
        )
        await session.commit()


async def find_stale_tiles(ttl_hours: int = 24, limit: int = 100) -> list[tuple[str, int, int, int]]:
    """Return (time_slot, z, x, y) for tiles older than ttl_hours."""
    async with async_session_factory() as session:
        result = await session.execute(
            text(
                "SELECT time_slot, zoom, tile_x, tile_y "
                "FROM tiles "
                "WHERE generated_at < now() - make_interval(hours => :ttl) "
                "ORDER BY generated_at ASC "
                "LIMIT :limit"
            ),
            {"ttl": ttl_hours, "limit": limit},
        )
        return [(r["time_slot"], r["zoom"], r["tile_x"], r["tile_y"]) for r in result.mappings()]


async def delete_all_tiles() -> int:
    """Delete all tiles from the database. Returns number of rows deleted."""
    async with async_session_factory() as session:
        result = await session.execute(text("DELETE FROM tiles"))
        await session.commit()
        deleted = result.rowcount
        logger.info("Deleted %d tiles from database", deleted)
        return deleted if deleted else 0


async def invalidate_tile_cache(cache_dir: str) -> None:
    """Remove all PNG files from the tile cache directory."""
    p = Path(cache_dir)
    if not p.exists():
        logger.warning("Tile cache directory does not exist: %s", cache_dir)
        return

    shutil.rmtree(p)
    p.mkdir(parents=True, exist_ok=True)
    logger.info("Cleared tile cache directory: %s", cache_dir)


async def calculate_tile_count(bbox: tuple[float, float, float, float], z_min: int, z_max: int) -> int:
    """Count the number of tiles in the given bbox across zoom levels."""
    min_lon, min_lat, max_lon, max_lat = bbox
    count = 0

    for z in range(z_min, z_max + 1):
        n = 2 ** z
        tile_size_deg = 360.0 / n

        x_min = math.floor((min_lon + 180.0) / tile_size_deg)
        x_max = math.floor((max_lon + 180.0) / tile_size_deg)
        y_min = math.floor((90.0 - max_lat) / tile_size_deg)
        y_max = math.floor((90.0 - min_lat) / tile_size_deg)

        x_min = max(0, x_min)
        x_max = min(n - 1, x_max)
        y_min = max(0, y_min)
        y_max = min(n - 1, y_max)

        if x_max < x_min or y_max < y_min:
            continue

        count += (x_max - x_min + 1) * (y_max - y_min + 1)

    return count
