"""Add spatial indexes on OSM tables.

Creates indexes on geometry columns to speed up spatial queries.
These indexes are critical for performance — without them, every tile
request does a full table scan on 1M+ rows.

Revision ID: 003_osm_spatial_indexes
Revises: 002_add_generated_at
"""

from typing import Sequence, Union

from alembic import op

revision: str = "003_osm_spatial_indexes"
down_revision: Union[str, None] = "002_add_generated_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Only create indexes if tables exist (osm_import may not have run yet)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'osm_roads') THEN
                CREATE INDEX IF NOT EXISTS idx_osm_roads_geom ON osm_roads USING GIST (geometry);
            END IF;
            IF EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'osm_areas') THEN
                CREATE INDEX IF NOT EXISTS idx_osm_areas_geom ON osm_areas USING GIST (geometry);
            END IF;
            IF EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'osm_settlements') THEN
                CREATE INDEX IF NOT EXISTS idx_osm_settlements_geom ON osm_settlements USING GIST (geometry);
            END IF;
            IF EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'osm_waterways') THEN
                CREATE INDEX IF NOT EXISTS idx_osm_waterways_geom ON osm_waterways USING GIST (geometry);
            END IF;
        END
        $$
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_osm_roads_geom")
    op.execute("DROP INDEX IF EXISTS idx_osm_areas_geom")
    op.execute("DROP INDEX IF EXISTS idx_osm_settlements_geom")
    op.execute("DROP INDEX IF EXISTS idx_osm_waterways_geom")
