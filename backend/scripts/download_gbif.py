#!/usr/bin/env python3
"""Download Ursus arctos occurrence records from GBIF and ingest into PostGIS.

Usage:
    python -m scripts.download_gbif

Environment:
    DATABASE_URL – PostgreSQL connection string (defaults to Settings value).

The script queries the GBIF Occurrence API for *Ursus arctos* inside the
pregeneration bbox (NW Russia / Northern Europe), then bulk-inserts every
record into the ``bear_observations`` table.  Duplicates are ignored via
``ON CONFLICT (gbif_id) DO NOTHING``.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import async_session_factory

logger = logging.getLogger(__name__)

GBIF_API = "https://api.gbif.org/v1/occurrence/search"
TAXON_KEY = 2433406  # Ursus arctos
PAGE_SIZE = 300
# Bbox from settings – same area as pregen_bbox
BBOX = settings.pregen_bbox  # "28.0,65.5,42.0,71.5"


def _iso_to_dt(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        # GBIF dates look like "2023-08-15T00:00:00.000+0000"
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_gbif_record(raw: dict[str, Any]) -> dict[str, Any]:
    """Flatten one GBIF result into kwargs for BearObservation."""
    return {
        "gbif_id": raw.get("key"),
        "event_date": _iso_to_dt(raw.get("eventDate")),
        "individual_count": raw.get("individualCount"),
        "basis_of_record": raw.get("basisOfRecord"),
        "lat": raw.get("decimalLatitude"),
        "lon": raw.get("decimalLongitude"),
        "country": raw.get("country"),
    }


async def _fetch_page(client: httpx.AsyncClient, offset: int) -> dict[str, Any]:
    params = {
        "taxonKey": TAXON_KEY,
        "decimalLongitude": f"{BBOX.split(',')[0]},{BBOX.split(',')[2]}",
        "decimalLatitude": f"{BBOX.split(',')[1]},{BBOX.split(',')[3]}",
        "limit": PAGE_SIZE,
        "offset": offset,
        "hasCoordinate": "true",
    }
    resp = await client.get(GBIF_API, params=params, timeout=60.0)
    resp.raise_for_status()
    return resp.json()


async def download_and_ingest(session: AsyncSession) -> int:
    """Fetch all pages from GBIF and insert into bear_observations."""
    inserted = 0
    async with httpx.AsyncClient() as client:
        # First page also tells us total count
        data = await _fetch_page(client, 0)
        total = data.get("count", 0)
        logger.info("GBIF reports %d Ursus arctos records in bbox %s", total, BBOX)

        if total == 0:
            return 0

        # Upsert statement – ignores duplicates
        upsert_sql = text(
            "INSERT INTO bear_observations "
            "(id, gbif_id, event_date, individual_count, basis_of_record, "
            " lat, lon, country, geom_4326) "
            "VALUES (gen_random_uuid(), :gbif_id, :event_date, :individual_count, "
            " :basis_of_record, :lat, :lon, :country, "
            " ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)) "
            "ON CONFLICT (gbif_id) DO NOTHING"
        )

        offset = 0
        while offset < total:
            if offset > 0:
                data = await _fetch_page(client, offset)

            results = data.get("results", [])
            if not results:
                break

            records = [_parse_gbif_record(r) for r in results]
            # Filter out records without valid numeric coordinates
            records = [r for r in records if isinstance(r.get("lat"), (int, float)) and isinstance(r.get("lon"), (int, float))]

            if records:
                await session.execute(upsert_sql, records)
                await session.commit()
                inserted += len(records)
                logger.info("Ingested %d / %d records", inserted, total)

            offset += len(results)

    logger.info("Done. Total inserted: %d", inserted)
    return inserted


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    async with async_session_factory() as session:
        count = await download_and_ingest(session)
    print(f"Inserted {count} new bear observations")


if __name__ == "__main__":
    asyncio.run(main())
