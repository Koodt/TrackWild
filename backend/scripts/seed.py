#!/usr/bin/env python3
"""Seed risk profiles from config/default_risk_profiles.json."""

import asyncio
import json
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.models.risk_profile import RiskProfile


async def seed_risk_profiles(session: AsyncSession) -> None:
    """Load and upsert risk profiles from JSON config."""
    config_path = Path(__file__).parent.parent / "config" / "default_risk_profiles.json"
    with open(config_path) as f:
        profiles = json.load(f)

    for prof in profiles:
        stmt = select(RiskProfile).where(
            RiskProfile.key == prof["key"],
            RiskProfile.value == prof["value"],
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            existing.base_risk = prof["base_risk"]
            existing.radius_m = prof["radius_m"]
            existing.geometry_type = prof["geometry_type"]
        else:
            session.add(RiskProfile(
                id=uuid.uuid4(),
                key=prof["key"],
                value=prof["value"],
                base_risk=prof["base_risk"],
                radius_m=prof["radius_m"],
                geometry_type=prof["geometry_type"],
            ))

    await session.commit()
    print(f"Seeded {len(profiles)} risk profiles")


async def seed() -> None:
    async with async_session_factory() as session:
        await seed_risk_profiles(session)


if __name__ == "__main__":
    asyncio.run(seed())
