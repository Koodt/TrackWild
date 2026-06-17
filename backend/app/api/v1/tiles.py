import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.config import settings
from app.services.tile_generator import _TRANSPARENT_PNG
from app.services.tile_service import get_tile
from app.services.tile_worker import enqueue, queue_size

router = APIRouter(prefix="/tiles", tags=["tiles"])

TIME_SLOT_PATTERN = re.compile(r"^[a-zA-Z0-9_]{1,30}$")


def validate_time_slot(time_slot: str) -> None:
    if not TIME_SLOT_PATTERN.match(time_slot):
        raise HTTPException(status_code=400, detail="Invalid time slot identifier")


def validate_coords(z: int, x: int, y: int) -> None:
    if z < 0 or z > 22:
        raise HTTPException(status_code=400, detail="Zoom level out of range")
    max_val = 2**z
    if x < 0 or x >= max_val or y < 0 or y >= max_val:
        raise HTTPException(status_code=400, detail="Tile coordinates out of range")


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

    If the tile is not cached, a transparent PNG is returned immediately
    and the tile is enqueued for background generation.
    On next request the generated tile will be served from cache.
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

    # --- Not cached: return transparent PNG + enqueue for background generation ---
    enqueue(time_slot, z, x, y)

    return Response(
        content=_TRANSPARENT_PNG,
        media_type="image/png",
        headers={
            "Cache-Control": "no-store",
            "X-Tile-Status": "pending",
        },
    )


@router.post(
    "/generate/{time_slot}/{z}/{x}/{y}",
    name="generate_tile",
)
async def generate_tile(
    time_slot: str,
    z: int,
    x: int,
    y: int,
) -> dict:
    """Enqueue a tile for background generation."""
    validate_time_slot(time_slot)
    validate_coords(z, x, y)
    enqueue(time_slot, z, x, y)
    return {
        "status": "enqueued",
        "time_slot": time_slot,
        "z": z,
        "x": x,
        "y": y,
        "queue_size": queue_size(),
    }


@router.get(
    "/queue-status",
    name="queue_status",
)
async def get_queue_status() -> dict:
    """Return current tile generation queue size."""
    return {"queue_size": queue_size()}
