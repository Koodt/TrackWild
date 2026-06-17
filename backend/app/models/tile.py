import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, SmallInteger, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Tile(Base):
    __tablename__ = "tiles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    time_slot: Mapped[str] = mapped_column(String(50), nullable=False)
    zoom: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    tile_x: Mapped[int] = mapped_column(Integer, nullable=False)
    tile_y: Mapped[int] = mapped_column(Integer, nullable=False)
    png_data: Mapped[bytes] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("time_slot", "zoom", "tile_x", "tile_y", name="uq_tile_key"),
    )
