import re

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.config import settings
from app.services.tile_service import get_tile

router = APIRouter(prefix="/tiles", tags=["tiles"])

# Strict pattern: time_slot must be a valid time identifier (e.g. "09", "14", "night")
TIME_SLOT_PATTERN = re.compile(r"^[a-zA-Z0-9_]{1,30}$")


def validate_time_slot(time_slot: str) -> None:
    """Reject time_slot values that could be used for path traversal."""
    if not TIME_SLOT_PATTERN.match(time_slot):
        raise HTTPException(
            status_code=400,
            detail="Invalid time slot identifier",
        )


@router.get(
    "/{time_slot}/{z}/{x}/{y}.png",
    name="get_tile",
    responses={
        200: {"content": {"image/png": {}}},
        404: {"description": "Tile not found"},
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
    Get a heatmap tile for the specified time slot and coordinates.

    Two-tier caching:
    1. Check file cache first
    2. If not found, query database and write back to file cache
    """
    # Validate time_slot to prevent path traversal
    validate_time_slot(time_slot)

    # Validate tile coordinates
    if z < 0 or z > 22:
        raise HTTPException(status_code=400, detail="Zoom level out of range")
    max_x = 2 ** z
    if x < 0 or x >= max_x or y < 0 or y >= max_x:
        raise HTTPException(status_code=400, detail="Tile coordinates out of range")

    from pathlib import Path

    cache_path = Path(settings.tile_cache_dir) / time_slot / str(z) / f"{x}/{y}.png"

    # Try file cache first
    if cache_path.exists():
        return Response(
            content=cache_path.read_bytes(),
            media_type="image/png",
            headers={
                "Cache-Control": "public, max-age=86400",
            },
        )

    # Query database
    png_data = await get_tile(time_slot, z, x, y, session=db)

    if png_data is None:
        raise HTTPException(status_code=404, detail="Tile not found")

    # Write to file cache
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(png_data)
    except Exception:
        # Cache write failure is non-fatal
        pass

    return Response(
        content=png_data,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=86400",
        },
    )
