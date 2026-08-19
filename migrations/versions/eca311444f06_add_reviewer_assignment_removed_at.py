"""add reviewer_assignment removed_at

Revision ID: eca311444f06
Revises: 1ae035b4cfb6
Create Date: 2026-08-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import reviewpulse.db.models  # custom column types (UtcDateTime)


# revision identifiers, used by Alembic.
revision: str = 'eca311444f06'
down_revision: Union[str, Sequence[str], None] = '1ae035b4cfb6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'reviewer_assignments',
        sa.Column('removed_at', reviewpulse.db.models.UtcDateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('reviewer_assignments', 'removed_at')
