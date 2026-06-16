from fastapi import APIRouter

from app.api.v1 import tiles

router = APIRouter(prefix="/v1")

router.include_router(tiles.router)
