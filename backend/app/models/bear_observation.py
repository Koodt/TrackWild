import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class BearObservation(Base):
    """Brown bear (Ursus arctos) observations from GBIF.

    Each row is one occurrence record.  A spatial GIST index on
    ``geom_4326`` allows fast density look-ups during tile generation.
    """

    __tablename__ = "bear_observations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    # GBIF occurrence key (unique external ID)
    gbif_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    # Event date as reported by GBIF
    event_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Count of individuals observed (may be NULL)
    individual_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Basis of record: HUMAN_OBSERVATION, MACHINE_OBSERVATION, etc.
    basis_of_record: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Latitude / longitude in EPSG:4326
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    # PostGIS point (created automatically via Alembic or the download script)
    geom_4326 = mapped_column(
        "geom_4326",
        # Use plain Text so we don't need geoalchemy2 dependency at import time;
        # the column type is set declaratively in Alembic.
        type_=String,
        nullable=True,
    )
    # Country / region name from GBIF
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # When the record was ingested into our DB
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    __table_args__ = (
        # Speed up “how many observations within X km of this point” queries
        Index("idx_bear_obs_geom_4326", "geom_4326", postgresql_using="GIST"),
        Index("idx_bear_obs_lat_lon", "lat", "lon"),
    )
