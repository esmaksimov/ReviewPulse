"""add review channel_post_deleted_at

Revision ID: f2a9c6d4e871
Revises: b7f4c1e29d05
Create Date: 2026-09-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import reviewpulse.db.models  # custom column types (UtcDateTime)


# revision identifiers, used by Alembic.
revision: str = 'f2a9c6d4e871'
down_revision: Union[str, Sequence[str], None] = 'b7f4c1e29d05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'reviews',
        sa.Column('channel_post_deleted_at', reviewpulse.db.models.UtcDateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('reviews', 'channel_post_deleted_at')
