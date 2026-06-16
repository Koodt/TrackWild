#!/usr/bin/env python3
"""Development seed script.

Inserts sample risk_profiles and demo tiles for local development.
"""

import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.models.tile import Tile


DEMO_TILES = [
    {"time_slot": "09", "zoom": 10, "tile_x": 512, "tile_y": 512},
    {"time_slot": "09", "zoom": 10, "tile_x": 513, "tile_y": 512},
    {"time_slot": "14", "zoom": 10, "tile_x": 512, "tile_y": 512},
]


async def seed_tiles(session: AsyncSession) -> None:
    """Insert demo tiles."""
    # Minimal 1x1 transparent PNG
    png_data = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
        b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    for tile_data in DEMO_TILES:
        tile = Tile(
            id=uuid.uuid4(),
            time_slot=tile_data["time_slot"],
            zoom=tile_data["zoom"],
            tile_x=tile_data["tile_x"],
            tile_y=tile_data["tile_y"],
            png_data=png_data,
        )
        session.add(tile)

    await session.commit()
    print(f"Seeded {len(DEMO_TILES)} demo tiles")


async def seed() -> None:
    """Run seed operations."""
    async with async_session_factory() as session:
        await seed_tiles(session)


if __name__ == "__main__":
    asyncio.run(seed())
