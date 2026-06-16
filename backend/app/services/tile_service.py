from typing import Optional

from sqlalchemy import select
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
    """
    Retrieve tile PNG data from database.

    Args:
        time_slot: Time slot identifier (e.g., "00", "01", ... "23")
        z: Zoom level
        x: Tile X coordinate
        y: Tile Y coordinate
        session: Optional existing session

    Returns:
        PNG bytes if found, None otherwise
    """
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
        row = result.scalar_one_or_none()
        return row
    finally:
        if close_session:
            await session.close()
