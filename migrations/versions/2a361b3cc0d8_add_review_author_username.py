"""add review author_username

Revision ID: 2a361b3cc0d8
Revises: eca311444f06
Create Date: 2026-08-19 00:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import reviewpulse.db.models  # custom column types (UtcDateTime)


# revision identifiers, used by Alembic.
revision: str = '2a361b3cc0d8'
down_revision: Union[str, Sequence[str], None] = 'eca311444f06'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('reviews', sa.Column('author_username', sa.String(length=64), nullable=True))
    op.create_index(
        op.f('ix_reviews_author_username'), 'reviews', ['author_username'], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_reviews_author_username'), table_name='reviews')
    op.drop_column('reviews', 'author_username')
