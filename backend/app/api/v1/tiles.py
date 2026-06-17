import asyncio
import re
from pathlib import Path

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Response
from rasterio.transform import from_bounds
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.config import settings
from app.services.tile_generator import generate_osm_tile_png
from app.services.tile_service import get_tile, save_tile

router = APIRouter(prefix="/tiles", tags=["tiles"])

TIME_SLOT_PATTERN = re.compile(r"^[a-zA-Z0-9_]{1,30}$")

TILE_SIZE = 256
WEB_MERCATOR_MIN = -20037508.34
WEB_MERCATOR_MAX = 20037508.34


def validate_time_slot(time_slot: str) -> None:
    if not TIME_SLOT_PATTERN.match(time_slot):
        raise HTTPException(status_code=400, detail="Invalid time slot identifier")


def validate_coords(z: int, x: int, y: int) -> None:
    if z < 0 or z > 22:
        raise HTTPException(status_code=400, detail="Zoom level out of range")
    max_val = 2**z
    if x < 0 or x >= max_val or y < 0 or y >= max_val:
        raise HTTPException(status_code=400, detail="Tile coordinates out of range")


def _hash_coord(z: int, x: int, y: int) -> int:
    """Deterministic hash from tile coords for reproducible randomness."""
    return (z * 73856093 ^ x * 19349669 ^ y * 83492791) & 0xFFFFFFFF


def generate_demo_tile_png(z: int, x: int, y: int) -> bytes:
    """Generate a demo heatmap tile with risk sources that fade with distance."""
    import rasterio.io

    h = _hash_coord(z, x, y)

    # Pixel grid
    xs = np.arange(TILE_SIZE, dtype=np.float32)
    ys = np.arange(TILE_SIZE, dtype=np.float32)
    xx, yy = np.meshgrid(xs, ys)

    # Place 3-5 "risk sources" at pseudo-random positions inside this tile
    rng = np.random.RandomState(h)
    n_sources = 3 + (h % 3)  # 3 to 5 sources
    data = np.zeros((TILE_SIZE, TILE_SIZE), dtype=np.float32)

    for i in range(n_sources):
        sx = rng.randint(30, TILE_SIZE - 30)
        sy = rng.randint(30, TILE_SIZE - 30)
        intensity = 0.4 + rng.random() * 0.6  # 0.4-1.0
        radius = 40 + rng.randint(0, 80)       # pixels

        dist = np.sqrt((xx - sx) ** 2 + (yy - sy) ** 2)
        # Gaussian falloff: high near source, fading outward
        falloff = intensity * np.exp(-(dist**2) / (2 * (radius / 2.5) ** 2))
        data = np.maximum(data, falloff)

    data = np.clip(data, 0.0, 1.0)

    # Colormap with transparency where there's no risk:
    # rgba: green(0) → yellow(0.5) → red(1.0), transparent at 0
    alpha = (data > 0.05).astype(np.uint8) * 255

    rgba = np.zeros((4, TILE_SIZE, TILE_SIZE), dtype=np.uint8)
    rgba[0] = (data * 255).astype(np.uint8)
    rgba[1] = ((1 - abs(data - 0.5) * 2) * 255).astype(np.uint8)
    rgba[2] = ((1 - data) * 0).astype(np.uint8)  # no blue
    rgba[3] = alpha

    buf = rasterio.io.MemoryFile()
    with buf.open(
        driver="PNG",
        height=TILE_SIZE,
        width=TILE_SIZE,
        count=4,
        dtype=np.uint8,
        transform=from_bounds(0, 0, TILE_SIZE, TILE_SIZE, TILE_SIZE, TILE_SIZE),
    ) as ds:
        ds.write(rgba)

    result = buf.read()
    buf.close()
    return result


@router.get(
    "/{time_slot}/{z}/{x}/{y}.png",
    name="get_tile",
    responses={
        200: {"content": {"image/png": {}}},
    },
)
async def get_tile_png(
    time_slot: str,
    z: int,
    x: int,
    y: int,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """
    Get a heatmap tile.

    Lookup order:
    1. File cache on disk
    2. PostGIS database
    3. Generate from OSM data (PostGIS query → rasterize)
    4. Demo tile (pseudo-random fallback)

    If a tile is generated, it's saved to DB+cache in background.
    """
    validate_time_slot(time_slot)
    validate_coords(z, x, y)

    cache_path = Path(settings.tile_cache_dir) / time_slot / str(z) / f"{x}/{y}.png"

    # --- Layer 1: file cache ---
    if cache_path.exists():
        return Response(
            content=cache_path.read_bytes(),
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    # --- Layer 2: database ---
    png_data = await get_tile(time_slot, z, x, y, session=db)

    if png_data is not None:
        # Write to file cache for next request
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(png_data)
        except Exception:
            pass

        return Response(
            content=png_data,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    # --- Layer 3: generate on-the-fly (OSM → demo fallback) ---
    png_data = await generate_osm_tile_png(z, x, y, time_slot, session=db)
    if png_data is None:
        png_data = generate_demo_tile_png(z, x, y)

    # Save to DB + file cache in background (don't block response)
    async def _persist() -> None:
        try:
            await save_tile(time_slot, z, x, y, png_data)
        except Exception:
            pass
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(png_data)
        except Exception:
            pass

    asyncio.create_task(_persist())

    return Response(
        content=png_data,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=300"},
    )
