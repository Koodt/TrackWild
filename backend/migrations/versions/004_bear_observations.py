"""Add bear_observations table for GBIF Ursus arctos data.

Creates a table to store brown-bear occurrence records downloaded
from GBIF, with a spatial GIST index for fast density queries
during tile generation.

Revision ID: 004_bear_observations
Revises: 003_osm_spatial_indexes
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import BigInteger, Column, DateTime, Float, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID

revision: str = "004_bear_observations"
down_revision: Union[str, None] = "003_osm_spatial_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bear_observations",
        Column("id", UUID(as_uuid=True), primary_key=True),
        Column("gbif_id", BigInteger, nullable=False, unique=True),
        Column("event_date", DateTime(timezone=True), nullable=True),
        Column("individual_count", Integer, nullable=True),
        Column("basis_of_record", String(50), nullable=True),
        Column("lat", Float, nullable=False),
        Column("lon", Float, nullable=False),
        Column("country", String(100), nullable=True),
        Column("created_at", DateTime(timezone=True), server_default=func.now()),
    )
    # Add PostGIS geometry column via raw SQL (avoids geoalchemy2 dependency)
    op.execute(
        "ALTER TABLE bear_observations "
        "ADD COLUMN geom_4326 Geometry('POINT', 4326)"
    )
    op.execute(
        "CREATE INDEX idx_bear_obs_geom_4326 "
        "ON bear_observations USING GIST (geom_4326)"
    )
    op.execute(
        "CREATE INDEX idx_bear_obs_lat_lon ON bear_observations (lat, lon)"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION update_bear_obs_geom()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.geom_4326 = ST_SetSRID(ST_MakePoint(NEW.lon, NEW.lat), 4326);
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_bear_obs_geom
        BEFORE INSERT OR UPDATE ON bear_observations
        FOR EACH ROW
        EXECUTE FUNCTION update_bear_obs_geom();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_bear_obs_geom ON bear_observations")
    op.execute("DROP FUNCTION IF EXISTS update_bear_obs_geom()")
    op.execute("DROP INDEX IF EXISTS idx_bear_obs_geom_4326")
    op.execute("DROP INDEX IF EXISTS idx_bear_obs_lat_lon")
    op.drop_table("bear_observations")
