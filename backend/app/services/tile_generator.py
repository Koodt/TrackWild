"""Real tile generation from OSM data via PostGIS rasterization.

Workflow:
1. Fetch all OSM features overlapping the tile bbox.
2. PostGIS applies ST_Buffer and risk coefficients (risk read from risk_profiles table).
3. Python only rasterizes the returned geometries (no Shapely needed).

Risk profiles are stored in the ``risk_profiles`` database table and joined
in-sql via PostGIS, allowing changes without code deployment.
"""

import asyncio
import functools
import json
import logging
import math
from typing import Optional

import numpy as np
from rasterio.features import rasterize
from rasterio.transform import from_bounds
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

TILE_SIZE = 256

# --- removed: unconditional BEAR_BASELINE_RISK filler ---
# Previously every pixel that had no OSM feature was painted green,
# masking the underlying base map.  Now pixels stay transparent
# (risk = 0) unless an OSM feature explicitly marks them.

# Time-slot multipliers are currently neutralised (all = 1.0).  Only "day"
# tiles are generated, and activity variation by time of day is disabled
# to reduce load and keep the visual layer consistent.
TIME_MULTIPLIERS: dict[str, float] = {
    "night": 1.0,
    "morning": 1.0,
    "day": 1.0,
    "evening": 1.0,
}

# ---------------------------------------------------------------------------
# SQL: fetch features + apply risk + buffer in PostGIS (no Shapely needed)
# ---------------------------------------------------------------------------
#
# Buffers are applied using ::geography so distances are real metres
# regardless of latitude, then transformed back to EPSG:3857 for
# rasterisation.  The search envelope must be expanded by the *maximum
# possible Mercator extent* of those buffers so that a feature whose
# source geometry sits outside the tile but whose real-metre buffer
# overlaps the tile is still fetched.
#
# Max radius_m = 15 000 (place=city).  At 70°N sec(lat) ≈ 2.92, so the
# buffer may reach ~43 800 m in Mercator units.  We round up to 50 000.
_MAX_SEARCH_BUFFER_MERCATOR = 50_000

_TILE_FEATURES_SQL = text("""
WITH
tile_bbox AS (
    SELECT ST_MakeEnvelope(:left, :bottom, :right, :top, 3857) AS env
),
search_env AS (
    SELECT ST_Expand(
        ST_MakeEnvelope(:left, :bottom, :right, :top, 3857),
        :expand
    ) AS env
),
roads AS (
    SELECT
        CASE WHEN rp.radius_m > 0 THEN
            ST_Transform(
                ST_Buffer(
                    geography(ST_Transform(r.geometry, 4326)),
                    rp.radius_m
                )::geometry,
                3857
            )
            ELSE r.geometry
        END AS geom,
        rp.base_risk AS risk
    FROM osm_roads r
    JOIN risk_profiles rp ON rp.key = 'highway' AND rp.value = r.highway
    WHERE ST_Intersects(r.geometry, (SELECT env FROM search_env))
),
areas AS (
    SELECT
        CASE WHEN rp.radius_m > 0 THEN
            ST_Transform(
                ST_Buffer(
                    geography(ST_Transform(a.geometry, 4326)),
                    rp.radius_m
                )::geometry,
                3857
            )
            ELSE a.geometry
        END AS geom,
        rp.base_risk AS risk
    FROM osm_areas a
    JOIN risk_profiles rp ON rp.key = a.feature_key AND rp.value = a.feature_value
    WHERE ST_Intersects(a.geometry, (SELECT env FROM search_env))
),
settlements AS (
    SELECT
        CASE WHEN rp.radius_m > 0 THEN
            ST_Transform(
                ST_Buffer(
                    geography(ST_Transform(s.geometry, 4326)),
                    rp.radius_m
                )::geometry,
                3857
            )
            ELSE s.geometry
        END AS geom,
        rp.base_risk AS risk
    FROM osm_settlements s
    JOIN risk_profiles rp ON rp.key = 'place' AND rp.value = s.place
    WHERE ST_Intersects(s.geometry, (SELECT env FROM search_env))
),
railways AS (
    SELECT
        CASE WHEN rp.radius_m > 0 THEN
            ST_Transform(
                ST_Buffer(
                    geography(ST_Transform(r.geometry, 4326)),
                    rp.radius_m
                )::geometry,
                3857
            )
            ELSE r.geometry
        END AS geom,
        rp.base_risk AS risk
    FROM osm_railways r
    JOIN risk_profiles rp ON rp.key = 'railway' AND rp.value = r.railway
    WHERE ST_Intersects(r.geometry, (SELECT env FROM search_env))
),
combined AS (
    SELECT geom, risk FROM roads
    UNION ALL
    SELECT geom, risk FROM areas
    UNION ALL
    SELECT geom, risk FROM settlements
    UNION ALL
    SELECT geom, risk FROM railways
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

# SQL: keep observations as *unbuffered* points first; Python will expand
# them into circles whose radius depends on the current zoom level so the
# visual patches scale naturally with the map.
_BEAR_GEOJSON_SQL = text("""
SELECT ST_AsGeoJSON(
    ST_Transform(
        ST_SetSRID(ST_MakePoint(lon, lat), 4326),
        3857
    )
) AS geojson
FROM bear_observations
WHERE lon BETWEEN :min_lon AND :max_lon
  AND lat BETWEEN :min_lat AND :max_lat
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


def _point_to_circle(center_geojson: dict, radius_m: float, segments: int = 32) -> Optional[dict]:
    """Approximate a circle as a regular polygon in EPSG:3857."""
    coords = center_geojson.get("coordinates")
    if not coords or len(coords) < 2:
        return None
    cx, cy = float(coords[0]), float(coords[1])
    angles = [2.0 * math.pi * i / segments for i in range(segments)]
    ring = [[cx + radius_m * math.cos(a), cy + radius_m * math.sin(a)] for a in angles]
    ring.append(ring[0])
    return {"type": "Polygon", "coordinates": [ring]}


def _mercator_to_lonlat(x: float, y: float) -> tuple[float, float]:
    """Convert Web Mercator (EPSG:3857) coordinates to WGS84 lon/lat."""
    lon = math.degrees(x / 6378137.0)
    lat = math.degrees(2.0 * math.atan(math.exp(y / 6378137.0)) - math.pi / 2.0)
    return lon, lat


def _bear_area_radius(z: int) -> float:
    """Radius (web-metres) for each GBIF point at given zoom.

    At low zooms (continent view) observations must merge into huge patches
    so the user sees entire ranges; at high zooms (street view) they shrink
    to the real error radius of a GPS observation (~10 m).
    """
    if z <= 5:
        return 50_000   # 50 km – whole-range patches
    if z <= 7:
        return 20_000   # 20 km – regional range
    if z <= 9:
        return 5_000    # 5 km – local clusters
    if z <= 11:
        return 1_000    # 1 km – precise sightings
    return 200          # 200 m – GPS accuracy at street level


def _buffer_shapes(
    positive_shapes: list[tuple[dict, float]],
    negative_shapes: list[tuple[dict, float]],
    transform,
) -> np.ndarray:
    """Rasterize GeoJSON shapes with proportional suppression.

    1. Start with zeros (transparent) everywhere.
    2. Positive shapes: rasterize and take maximum (no baseline filler).
    3. Negative shapes: build suppression mask and multiply.

    Urban features (-1.0) fully suppress (multiplier 0).
    Neutral features (0.0) are ignored entirely.
    Pixels without any feature remain transparent.
    """
    out = np.zeros((TILE_SIZE, TILE_SIZE), dtype=np.float32)

    # Step 1: positive risk (max merge via sorted + rasterize)
    if positive_shapes:
        pos_sorted = sorted(positive_shapes, key=lambda item: item[1])
        pos_raster = rasterize(
            zip((s[0] for s in pos_sorted), (s[1] for s in pos_sorted)),
            out_shape=(TILE_SIZE, TILE_SIZE),
            transform=transform,
            fill=0.0,
            dtype=np.float32,
            all_touched=True,
        )
        out = np.maximum(out, pos_raster)

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
        np.clip(suppress, 0.0, 1.0, out=suppress)
        out = out * (1.0 - suppress)

    return out


def _apply_risk_to_features(
    geojson_shapes: list[dict],
    risks: list[float],
    time_slot: str,
) -> tuple[list[tuple[dict, float]], list[tuple[dict, float]]]:
    """Apply time multiplier to risks.

    Returns:
        (positive_shapes, negative_shapes)
    Features with base_risk == 0 are ignored (neutral, neither positive nor
    negative).
    """
    mult = TIME_MULTIPLIERS.get(time_slot, 1.0)

    positive: list[tuple[dict, float]] = []
    negative: list[tuple[dict, float]] = []

    for geojson, base_risk in zip(geojson_shapes, risks):
        # Skip truly neutral features (bare_rock, scree, sand, etc.)
        if base_risk == 0.0:
            continue

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
    rgba[3] = (v > 0.15).astype(np.uint8) * 255                 # alpha
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


@functools.lru_cache(maxsize=1)
def _get_transparent_png() -> bytes:
    """Lazy transparent PNG (256×256, all alpha=0).

    Cached via lru_cache so rasterio is only initialized on first call,
    not at module-import time.
    """
    import rasterio.io

    buf = rasterio.io.MemoryFile()
    with buf.open(
        driver="PNG",
        height=TILE_SIZE,
        width=TILE_SIZE,
        count=4,
        dtype=np.uint8,
        transform=from_bounds(0, 0, TILE_SIZE, TILE_SIZE, TILE_SIZE, TILE_SIZE),
    ) as ds:
        ds.write(_empty_rgba())
    data = buf.read()
    buf.close()
    return data


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def _has_osm_tables(session: AsyncSession) -> bool:
    """Return True if all required OSM tables exist in public schema.

    Required tables: osm_roads, osm_areas, osm_settlements, osm_railways.
    """
    stmt = text("""
        SELECT COUNT(*) = 4
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name IN ('osm_roads', 'osm_areas', 'osm_settlements', 'osm_railways')
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
    """Generate a heatmap tile from real OSM data + GBIF observations.

    Returns ``None`` when OSM tables are absent or the query fails.
    GBIF observations are added as an unconditional positive layer so that
    areas without OSM features (e.g. Kola tundra) still render risk patches.
    """
    if not await _has_osm_tables(session):
        return None

    transform = from_bounds(*_tile_bbox(z, x, y), TILE_SIZE, TILE_SIZE)
    left, bottom, right, top = _tile_bbox(z, x, y)

    # --- 1. Pull OSM features ---
    try:
        result = await session.execute(
            _TILE_FEATURES_SQL,
            {
                "left": left,
                "bottom": bottom,
                "right": right,
                "top": top,
                "expand": _MAX_SEARCH_BUFFER_MERCATOR,
            },
        )
        osm_rows = result.mappings().all()
    except Exception as exc:
        logger.warning("PostGIS tile query failed: %s", exc)
        return None

    # --- 2. Pull GBIF observations as unconditional positive layer ---
    gbif_positive: list[tuple[dict, float]] = []
    try:
        # Convert tile bbox corners to 4326 for lat/lon bounding
        min_lon, max_lat = _mercator_to_lonlat(left, top)
        max_lon, min_lat = _mercator_to_lonlat(right, bottom)
        radius = _bear_area_radius(z)
        # Pad bounding box by radius in degrees (~111km per degree)
        pad_deg = max(radius / 111_320.0, 0.01)

        bear_result = await session.execute(
            _BEAR_GEOJSON_SQL,
            {
                "min_lon": min_lon - pad_deg,
                "max_lon": max_lon + pad_deg,
                "min_lat": max(min_lat - pad_deg, -90.0),
                "max_lat": min(max_lat + pad_deg, 90.0),
            },
        )
        for row in bear_result.mappings().all():
            pt = json.loads(row["geojson"])
            circle = _point_to_circle(pt, radius)
            if circle:
                gbif_positive.append((circle, 0.4))

        if gbif_positive:
            logger.debug(
                "Tile z=%d x=%d y=%d: %d GBIF obs, radius=%.0f m",
                z, x, y, len(gbif_positive), radius,
            )
    except Exception as exc:
        # Non-fatal: if table/index missing, log and continue without GBIF
        logger.debug("GBIF query skipped: %s", exc)

    # --- 3. Parse OSM features ---
    positive_shapes: list[tuple[dict, float]] = []
    negative_shapes: list[tuple[dict, float]] = []

    if osm_rows:
        geojson_shapes = [json.loads(r["geojson"]) for r in osm_rows]
        risks = [float(r["risk"]) for r in osm_rows]
        p, n = _apply_risk_to_features(geojson_shapes, risks, time_slot)
        positive_shapes.extend(p)
        negative_shapes.extend(n)

    # Merge GBIF into positive shapes (max merge inside _buffer_shapes handles priority)
    positive_shapes.extend(gbif_positive)

    # Nothing renderable at all
    if not positive_shapes and not negative_shapes:
        return None

    loop = asyncio.get_running_loop()
    raster = await loop.run_in_executor(
        None, _buffer_shapes, positive_shapes, negative_shapes, transform
    )

    rgba = _colormap(raster)
    return await loop.run_in_executor(None, _to_png, rgba, transform)
