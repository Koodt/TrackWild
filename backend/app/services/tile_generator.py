"""Real tile generation from OSM data via PostGIS rasterization.

Workflow:
1. Check that OSM tables (osm_roads, osm_areas, osm_settlements) exist.
2. Fetch all risk-relevant features overlapping the tile bbox (EPSG:3857).
3. Buffer lines/points by radius_m from risk_profiles.
4. Rasterize in Python with max-merge (sort ascending risk, replace keeps highest).
5. Apply green→yellow→red colormap with transparency on zero risk.

Returns None if OSM data unavailable → caller falls back to demo tiles.
"""

import asyncio
import json
import logging
from typing import Optional

import numpy as np
from rasterio.features import rasterize
from rasterio.transform import from_bounds
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

TILE_SIZE = 256

# Activity modifiers per time of day (wildlife is more active at night/dusk)
TIME_MULTIPLIERS: dict[str, float] = {
    "night": 1.2,
    "morning": 1.0,
    "day": 0.8,
    "evening": 1.1,
}

# ---------------------------------------------------------------------------
# SQL: fetch all risk-relevant geometries that overlap the tile bbox
# ---------------------------------------------------------------------------
_TILE_FEATURES_SQL = text("""
WITH
tile_env AS (
    SELECT ST_MakeEnvelope(:left, :bottom, :right, :top, 3857) AS env3857
),
roads AS (
    SELECT
        ST_Buffer(ST_Transform(r.geometry, 3857), rp.radius_m) AS geom,
        rp.base_risk AS risk
    FROM osm_roads r
    JOIN risk_profiles rp
        ON rp.key = 'highway'
        AND rp.value = r.highway
    WHERE ST_Intersects(ST_Transform(r.geometry, 3857), (SELECT env3857 FROM tile_env))
),
areas AS (
    SELECT
        ST_Transform(a.geometry, 3857) AS geom,
        rp.base_risk AS risk
    FROM osm_areas a
    JOIN risk_profiles rp
        ON rp.key = a.feature_key
        AND rp.value = a.feature_value
    WHERE ST_Intersects(ST_Transform(a.geometry, 3857), (SELECT env3857 FROM tile_env))
),
settlements AS (
    SELECT
        ST_Buffer(ST_Transform(s.geometry, 3857), rp.radius_m) AS geom,
        rp.base_risk AS risk
    FROM osm_settlements s
    JOIN risk_profiles rp
        ON rp.key = 'place'
        AND rp.value = s.place
    WHERE ST_Intersects(ST_Transform(s.geometry, 3857), (SELECT env3857 FROM tile_env))
),
combined AS (
    SELECT geom, risk FROM roads
    UNION ALL
    SELECT geom, risk FROM areas
    UNION ALL
    SELECT geom, risk FROM settlements
)
SELECT ST_AsGeoJSON(geom) AS geojson, risk
FROM combined
WHERE geom IS NOT NULL
  AND NOT ST_IsEmpty(geom)
""")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _tile_bbox(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """Web Mercator bbox for tile (left, bottom, right, top)."""
    n = 2.0**z
    tile_size_m = 40075016.68557849 / n
    left = -20037508.342789244 + x * tile_size_m
    right = left + tile_size_m
    top = 20037508.342789244 - y * tile_size_m
    bottom = top - tile_size_m
    return left, bottom, right, top


def _empty_rgba() -> np.ndarray:
    """Fully transparent 256x256 RGBA array."""
    return np.zeros((4, TILE_SIZE, TILE_SIZE), dtype=np.uint8)


def _rasterize_max(
    shapes: list[tuple[dict, float]],
    transform,
) -> np.ndarray:
    """Rasterize GeoJSON shapes; higher risk wins on overlap.

    Strategy: sort shapes by risk ascending, then rasterize with
    ``merge_alg=replace``. Since replace keeps the *last* value at each
    pixel, the highest risk prevails.
    """
    if not shapes:
        return np.zeros((TILE_SIZE, TILE_SIZE), dtype=np.float32)

    shapes.sort(key=lambda item: item[1])
    return rasterize(
        zip((s[0] for s in shapes), (s[1] for s in shapes)),
        out_shape=(TILE_SIZE, TILE_SIZE),
        transform=transform,
        fill=0.0,
        dtype=np.float32,
        all_touched=True,
    )


def _colormap(risk: np.ndarray) -> np.ndarray:
    """Map float risk [0, 1] → RGBA uint8 (green → yellow → red)."""
    v = np.clip(risk, 0.0, 1.0)
    rgba = np.zeros((4, TILE_SIZE, TILE_SIZE), dtype=np.uint8)
    rgba[0] = (np.clip(v * 2, 0, 1) * 255).astype(np.uint8)   # red
    rgba[1] = (np.clip(2 - v * 2, 0, 1) * 255).astype(np.uint8)  # green
    rgba[2] = 0                                                   # blue
    rgba[3] = (v > 0.05).astype(np.uint8) * 255                  # alpha
    return rgba


def _to_png(rgba: np.ndarray, transform) -> bytes:
    """Write RGBA array to PNG bytes via rasterio."""
    import rasterio.io

    buf = rasterio.io.MemoryFile()
    with buf.open(
        driver="PNG",
        height=TILE_SIZE,
        width=TILE_SIZE,
        count=4,
        dtype=np.uint8,
        transform=transform,
    ) as ds:
        ds.write(rgba)
    data = buf.read()
    buf.close()
    return data


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def _has_osm_tables(session: AsyncSession) -> bool:
    """Return True if osm_roads table exists in public schema."""
    stmt = text("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name   = 'osm_roads'
        )
    """)
    result = await session.execute(stmt)
    return bool(result.scalar_one_or_none())


async def generate_osm_tile_png(
    z: int,
    x: int,
    y: int,
    time_slot: str,
    session: AsyncSession,
) -> Optional[bytes]:
    """Generate a heatmap tile from real OSM data.

    Returns ``None`` when OSM tables are absent, empty, or the query fails,
    signalling the caller to fall back to demo generation.
    """
    if not await _has_osm_tables(session):
        return None

    mult = TIME_MULTIPLIERS.get(time_slot, 1.0)
    transform = from_bounds(*_tile_bbox(z, x, y), TILE_SIZE, TILE_SIZE)

    left, bottom, right, top = _tile_bbox(z, x, y)

    try:
        result = await session.execute(
            _TILE_FEATURES_SQL,
            {"left": left, "bottom": bottom, "right": right, "top": top},
        )
        rows = result.mappings().all()
    except Exception as exc:
        logger.warning("PostGIS tile query failed: %s", exc)
        return None

    if not rows:
        # No features in this tile → transparent
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, _to_png, _empty_rgba(), transform
        )

    # Parse geojson + apply time multiplier
    shapes: list[tuple[dict, float]] = []
    for row in rows:
        geojson = json.loads(row["geojson"])
        risk = min(float(row["risk"]) * mult, 1.0)
        shapes.append((geojson, risk))

    # CPU-heavy rasterization offloaded to thread pool
    loop = asyncio.get_running_loop()
    raster = await loop.run_in_executor(None, _rasterize_max, shapes, transform)
    rgba = _colormap(raster)
    return await loop.run_in_executor(None, _to_png, rgba, transform)
