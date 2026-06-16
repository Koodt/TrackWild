import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TileBase(BaseModel):
    time_slot: str
    zoom: int
    tile_x: int
    tile_y: int


class TileCreate(TileBase):
    png_data: bytes


class TileResponse(TileBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime


class TileNotFound(BaseModel):
    detail: str = "Tile not found"


class HealthResponse(BaseModel):
    status: str
    database: str
