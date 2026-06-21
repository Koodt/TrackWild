import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class RiskProfile(Base):
    __tablename__ = "risk_profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    key: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    value: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    base_risk: Mapped[float] = mapped_column(Float, nullable=False)
    radius_m: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    geometry_type: Mapped[str] = mapped_column(String(10), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
