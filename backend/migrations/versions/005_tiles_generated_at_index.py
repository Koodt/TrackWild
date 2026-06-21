"""Add index on tiles.generated_at for stale-tile queries.

Revision ID: 005_tiles_generated_at_index
Revises: 004_bear_observations
"""

from typing import Sequence, Union

from alembic import op

revision: str = "005_tiles_generated_at_index"
down_revision: Union[str, None] = "004_bear_observations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("idx_tiles_generated_at", "tiles", ["generated_at"])


def downgrade() -> None:
    op.drop_index("idx_tiles_generated_at", table_name="tiles")
