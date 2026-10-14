"""add review channel_cleanup_abandoned_at

Revision ID: c9f17a2e5db4
Revises: a3c8f91e2b47
Create Date: 2026-10-15 01:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import reviewpulse.db.models  # custom column types (UtcDateTime)


# revision identifiers, used by Alembic.
revision: str = 'c9f17a2e5db4'
down_revision: Union[str, Sequence[str], None] = 'a3c8f91e2b47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'reviews',
        sa.Column(
            'channel_cleanup_abandoned_at',
            reviewpulse.db.models.UtcDateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('reviews', 'channel_cleanup_abandoned_at')
