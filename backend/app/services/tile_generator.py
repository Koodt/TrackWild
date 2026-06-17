"""Real tile generation from OSM data via PostGIS rasterization.

Workflow:
1. Fetch all OSM features overlapping the tile bbox (no risk_profiles JOIN).
2. Apply risk coefficients from external JSON (risk_config).
3. Buffer lines/points by radius_m from risk_config.
4. Rasterize in Python with max-merge.
5. Apply green→yellow→red colormap with transparency on zero risk.

Risk profiles are read from /app/config/risk_profiles.json (Docker volume),
allowing changes without container rebuild.
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

from app.services.risk_config import risk_config

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
# SQL: fetch all OSM features overlapping the tile bbox (no risk_profiles JOIN)
# ---------------------------------------------------------------------------
_TILE_FEATURES_SQL = text("""
WITH
tile_env AS (
    SELECT ST_MakeEnvelope(:left, :bottom, :right, :top, 3857) AS env3857
),
roads AS (
    SELECT
        ST_Transform(r.geometry, 3857) AS geom,
        r.highway AS feature_value
    FROM osm_roads r
    WHERE ST_Intersects(ST_Transform(r.geometry, 3857), (SELECT env3857 FROM tile_env))
),
areas AS (
    SELECT
        ST_Transform(a.geometry, 3857) AS geom,
        a.feature_value AS feature_value,
        a.feature_key AS feature_key
    FROM osm_areas a
    WHERE ST_Intersects(ST_Transform(a.geometry, 3857), (SELECT env3857 FROM tile_env))
),
settlements AS (
    SELECT
        ST_Transform(s.geometry, 3857) AS geom,
        s.place AS feature_value
    FROM osm_settlements s
    WHERE ST_Intersects(ST_Transform(s.geometry, 3857), (SELECT env3857 FROM tile_env))
),
combined AS (
    SELECT geom, 'highway' AS feature_key, feature_value FROM roads
    UNION ALL
    SELECT geom, feature_key, feature_value FROM areas
    UNION ALL
    SELECT geom, 'place' AS feature_key, feature_value FROM settlements
)
SELECT ST_AsGeoJSON(geom) AS geojson, feature_key, feature_value
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
    positive_shapes: list[tuple[dict, float]],
    negative_shapes: list[tuple[dict, float]],
    transform,
) -> np.ndarray:
    """Rasterize GeoJSON shapes with suppressor support.

    1. Rasterize positive shapes with max-merge (higher risk wins).
    2. Rasterize negative shapes as a mask — zero out risk where infrastructure is.
    """
    out = np.zeros((TILE_SIZE, TILE_SIZE), dtype=np.float32)

    # Step 1: positive risk (max merge)
    if positive_shapes:
        pos_sorted = sorted(positive_shapes, key=lambda item: item[1])
        out = rasterize(
            zip((s[0] for s in pos_sorted), (s[1] for s in pos_sorted)),
            out_shape=(TILE_SIZE, TILE_SIZE),
            transform=transform,
            fill=0.0,
            dtype=np.float32,
            all_touched=True,
        )

    # Step 2: negative mask — zero out risk where suppressors exist
    if negative_shapes:
        mask = rasterize(
            zip((s[0] for s in negative_shapes), (1 for _ in negative_shapes)),
            out_shape=(TILE_SIZE, TILE_SIZE),
            fill=0,
            dtype=np.uint8,
            all_touched=True,
            transform=transform,
        )
        out[mask > 0] = 0.0

    return out


def _apply_risk_to_features(
    geojson_shapes: list[dict],
    feature_keys: list[str],
    feature_values: list[str],
    time_slot: str,
) -> tuple[list[tuple[dict, float]], list[tuple[dict, float]]]:
    """Apply risk coefficients from external JSON config to raw OSM features.

    Returns (positive_shapes, negative_shapes) with buffering already applied.
    Positive shapes add risk; negative shapes zero it out (suppressors).
    """
    from shapely.geometry import shape, mapping

    lookup = risk_config.get_lookup()
    mult = TIME_MULTIPLIERS.get(time_slot, 1.0)

    positive: list[tuple[dict, float]] = []
    negative: list[tuple[dict, float]] = []

    for geojson, fkey, fval in zip(geojson_shapes, feature_keys, feature_values):
        profile = lookup.get((fkey, fval))
        if profile is None:
            continue

        base_risk = float(profile["base_risk"])
        radius_m = int(profile["radius_m"])
        geom = shape(geojson)

        if radius_m > 0:
            geom = geom.buffer(radius_m)

        if geom.is_empty or not geom.is_valid:
            continue

        risk = min(abs(base_risk) * mult, 1.0)
        gjson = mapping(geom)

        if base_risk < 0:
            negative.append((gjson, risk))
        else:
            positive.append((gjson, risk))

    return positive, negative


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


# Pre-computed transparent PNG (256×256, all alpha=0)
_TRANSPARENT_PNG: bytes = _to_png(
    _empty_rgba(),
    from_bounds(0, 0, TILE_SIZE, TILE_SIZE, TILE_SIZE, TILE_SIZE),
)


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

    Returns ``None`` when OSM tables are absent or the query fails.
    """
    if not await _has_osm_tables(session):
        return None

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
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, _to_png, _empty_rgba(), transform
        )

    # Parse features and apply risk from external config
    geojson_shapes = [json.loads(row["geojson"]) for row in rows]
    feature_keys = [row["feature_key"] for row in rows]
    feature_values = [row["feature_value"] for row in rows]

    shapes = _apply_risk_to_features(geojson_shapes, feature_keys, feature_values, time_slot)
    positive_shapes, negative_shapes = shapes

    loop = asyncio.get_running_loop()
    raster = await loop.run_in_executor(
        None, _rasterize_max, positive_shapes, negative_shapes, transform
    )
    rgba = _colormap(raster)
    return await loop.run_in_executor(None, _to_png, rgba, transform)
