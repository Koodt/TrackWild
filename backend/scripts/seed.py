#!/usr/bin/env python3
"""Development seed script.

Inserts risk_profiles from config/default_risk_profiles.json and demo tiles.
"""

import asyncio
import json
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.models.risk_profile import RiskProfile
from app.models.tile import Tile


DEMO_TILES = [
    {"time_slot": "09", "zoom": 10, "tile_x": 512, "tile_y": 512},
    {"time_slot": "09", "zoom": 10, "tile_x": 513, "tile_y": 512},
    {"time_slot": "14", "zoom": 10, "tile_x": 512, "tile_y": 512},
]

# Minimal 1x1 transparent PNG
TRANSPARENT_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


async def seed_risk_profiles(session: AsyncSession) -> None:
    """Load and insert risk profiles from JSON config."""
    config_path = Path(__file__).parent.parent / "config" / "default_risk_profiles.json"
    with open(config_path) as f:
        profiles = json.load(f)

    for prof in profiles:
        # Check if profile already exists
        stmt = select(RiskProfile).where(
            RiskProfile.key == prof["key"],
            RiskProfile.value == prof["value"],
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            # Update existing profile
            existing.base_risk = prof["base_risk"]
            existing.radius_m = prof["radius_m"]
            existing.geometry_type = prof["geometry_type"]
        else:
            # Insert new profile
            rp = RiskProfile(
                id=uuid.uuid4(),
                key=prof["key"],
                value=prof["value"],
                base_risk=prof["base_risk"],
                radius_m=prof["radius_m"],
                geometry_type=prof["geometry_type"],
            )
            session.add(rp)

    await session.commit()
    print(f"Seeded {len(profiles)} risk profiles")


async def seed_tiles(session: AsyncSession) -> None:
    """Insert demo tiles."""
    for tile_data in DEMO_TILES:
        # Check if tile already exists
        stmt = select(Tile).where(
            Tile.time_slot == tile_data["time_slot"],
            Tile.zoom == tile_data["zoom"],
            Tile.tile_x == tile_data["tile_x"],
            Tile.tile_y == tile_data["tile_y"],
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            continue

        tile = Tile(
            id=uuid.uuid4(),
            time_slot=tile_data["time_slot"],
            zoom=tile_data["zoom"],
            tile_x=tile_data["tile_x"],
            tile_y=tile_data["tile_y"],
            png_data=TRANSPARENT_PNG,
        )
        session.add(tile)

    await session.commit()
    print(f"Seeded {len(DEMO_TILES)} demo tiles")


async def seed() -> None:
    """Run seed operations."""
    async with async_session_factory() as session:
        await seed_risk_profiles(session)
        await seed_tiles(session)


if __name__ == "__main__":
    asyncio.run(seed())
