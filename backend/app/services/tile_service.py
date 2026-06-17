import uuid
from typing import Optional

from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.models.tile import Tile


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

    close_session = False
    if session is None:
        session = async_session_factory()
        close_session = True

    try:
        result = await session.execute(query)
        return result.scalar_one_or_none()
    finally:
        if close_session:
            await session.close()


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
            text("""
                INSERT INTO tiles (id, time_slot, zoom, tile_x, tile_y, png_data)
                VALUES (:id, :time_slot, :zoom, :tile_x, :tile_y, :png_data)
                ON CONFLICT (time_slot, zoom, tile_x, tile_y)
                DO UPDATE SET png_data = EXCLUDED.png_data
            """"),
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
