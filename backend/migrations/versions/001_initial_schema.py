"""Initial schema: tiles and risk_profiles tables.

Revision ID: 001
Revises:
Create Date: 2026-01-01 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable PostGIS extension
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    # Create risk_profiles table
    op.create_table(
        "risk_profiles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("key", sa.String(50), nullable=False, index=True),
        sa.Column("value", sa.String(100), nullable=False, index=True),
        sa.Column("base_risk", sa.Float(), nullable=False),
        sa.Column("radius_m", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("geometry_type", sa.String(10), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            onupdate=sa.text("now()"),
        ),
    )

    # Create tiles table
    op.create_table(
        "tiles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("time_slot", sa.String(50), nullable=False),
        sa.Column("zoom", sa.SmallInteger(), nullable=False),
        sa.Column("tile_x", sa.Integer(), nullable=False),
        sa.Column("tile_y", sa.Integer(), nullable=False),
        sa.Column("png_data", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "time_slot", "zoom", "tile_x", "tile_y",
            name="uq_tile_key",
        ),
    )


def downgrade() -> None:
    op.drop_table("tiles")
    op.drop_table("risk_profiles")
    op.execute("DROP EXTENSION IF EXISTS postgis")
