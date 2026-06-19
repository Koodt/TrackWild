"""Real tile generation from OSM data via PostGIS rasterization.

Workflow:
1. Fetch all OSM features overlapping the tile bbox.
2. PostGIS applies ST_Buffer and risk coefficients (risk passed as JSON parameter).
3. Python only rasterize the returned geometries (no Shapely needed).

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
# SQL: fetch features + apply risk + buffer in PostGIS (no Shapely needed)
# ---------------------------------------------------------------------------
_TILE_FEATURES_SQL = text("""
WITH
tile_env AS (
    SELECT ST_MakeEnvelope(:left, :bottom, :right, :top, 3857) AS env
),
roads AS (
    SELECT
        CASE WHEN rp.radius_m > 0
            THEN ST_Buffer(r.geometry, rp.radius_m)
            ELSE r.geometry
        END AS geom,
        rp.base_risk AS risk
    FROM osm_roads r
    JOIN risk_profiles rp ON rp.key = 'highway' AND rp.value = r.highway
    WHERE ST_Intersects(r.geometry, (SELECT env FROM tile_env))
),
areas AS (
    SELECT a.geometry AS geom,
        rp.base_risk AS risk
    FROM osm_areas a
    JOIN risk_profiles rp ON rp.key = a.feature_key AND rp.value = a.feature_value
    WHERE ST_Intersects(a.geometry, (SELECT env FROM tile_env))
),
settlements AS (
    SELECT
        CASE WHEN rp.radius_m > 0
            THEN ST_Buffer(s.geometry, rp.radius_m)
            ELSE s.geometry
        END AS geom,
        rp.base_risk AS risk
    FROM osm_settlements s
    JOIN risk_profiles rp ON rp.key = 'place' AND rp.value = s.place
    WHERE ST_Intersects(s.geometry, (SELECT env FROM tile_env))
),
waterways AS (
    SELECT
        CASE WHEN rp.radius_m > 0
            THEN ST_Buffer(w.geometry, rp.radius_m)
            ELSE w.geometry
        END AS geom,
        rp.base_risk AS risk
    FROM osm_waterways w
    JOIN risk_profiles rp ON rp.key = 'waterway' AND rp.value = w.waterway
    WHERE ST_Intersects(w.geometry, (SELECT env FROM tile_env))
),
combined AS (
    SELECT geom, risk FROM roads
    UNION ALL
    SELECT geom, risk FROM areas
    UNION ALL
    SELECT geom, risk FROM settlements
    UNION ALL
    SELECT geom, risk FROM waterways
),
grouped AS (
    SELECT ST_Union(geom) AS geom, risk
    FROM combined
    WHERE geom IS NOT NULL
      AND NOT ST_IsEmpty(geom)
    GROUP BY risk
)
SELECT ST_AsGeoJSON(geom) AS geojson, risk
FROM grouped
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

    1. Rasterize positive risk (higher value wins on overlap).
    2. Build a suppression mask from negative shapes — areas where
       infrastructure dominates get their risk reduced proportionally.
       A suppression value of 1.0 means the area is fully developed
       (no wildlife risk at all).
    3. final_risk = positive_risk * (1.0 - min(suppression, 1.0))

    This means: inside a city centre (suppression ≈ 1.0), even if there's
    a small park (risk 0.7), it becomes 0.7 * (1 - 1.0) = 0.
    On the edge of town (suppression ≈ 0.5), the park shows as 0.35.
    """
    out = np.zeros((TILE_SIZE, TILE_SIZE), dtype=np.float32)

    # Step 1: positive risk (max merge via sorted + rasterize)
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

    # Step 2: build suppression mask (max of all suppressors)
    if negative_shapes:
        neg_sorted = sorted(negative_shapes, key=lambda item: item[1])
        suppress = rasterize(
            zip((s[0] for s in neg_sorted), (s[1] for s in neg_sorted)),
            out_shape=(TILE_SIZE, TILE_SIZE),
            transform=transform,
            fill=0.0,
            dtype=np.float32,
            all_touched=True,
        )
        # Suppression is multiplicative: city park is not wild
        # suppress values are clamped to [0, 1]
        np.clip(suppress, 0.0, 1.0, out=suppress)
        out = out * (1.0 - suppress)

    return out


def _apply_risk_to_features(
    geojson_shapes: list[dict],
    risks: list[float],
    time_slot: str,
) -> tuple[list[tuple[dict, float]], list[tuple[dict, float]]]:
    """Apply time multiplier to risks.

    PostGIS already did the buffering, so we only apply the time multiplier
    and split into positive/negative lists. No Shapely needed.

    Returns:
        (positive_shapes, negative_shapes)
    """
    mult = TIME_MULTIPLIERS.get(time_slot, 1.0)

    positive: list[tuple[dict, float]] = []
    negative: list[tuple[dict, float]] = []

    for geojson, base_risk in zip(geojson_shapes, risks):
        risk = min(abs(base_risk) * mult, 1.0)

        if base_risk < 0:
            negative.append((geojson, risk))
        else:
            positive.append((geojson, risk))

    return positive, negative


def _colormap(risk: np.ndarray) -> np.ndarray:
    """Map float risk [0, 1] → RGBA uint8 (green → yellow → red)."""
    v = np.clip(risk, 0.0, 1.0)
    rgba = np.zeros((4, TILE_SIZE, TILE_SIZE), dtype=np.uint8)
    rgba[0] = (np.clip(v * 2, 0, 1) * 255).astype(np.uint8)   # red
    rgba[1] = (np.clip(2 - v * 2, 0, 1) * 255).astype(np.uint8)  # green
    rgba[2] = 0                                                   # blue
    rgba[3] = (v > 0.15).astype(np.uint8) * 255                 # alpha — only draw if risk > 15%
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
        return None

    # Parse features — SQL already applied risk and buffer
    geojson_shapes = [json.loads(row["geojson"]) for row in rows]
    risks = [float(row["risk"]) for row in rows]

    positive_shapes, negative_shapes = _apply_risk_to_features(geojson_shapes, risks, time_slot)

    loop = asyncio.get_running_loop()
    raster = await loop.run_in_executor(
        None, _rasterize_max, positive_shapes, negative_shapes, transform
    )
    rgba = _colormap(raster)
    return await loop.run_in_executor(None, _to_png, rgba, transform)
