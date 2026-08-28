"""add announcement_draft description

Revision ID: b7f4c1e29d05
Revises: d3211b9001aa
Create Date: 2026-08-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7f4c1e29d05'
down_revision: Union[str, Sequence[str], None] = 'd3211b9001aa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'announcement_drafts',
        sa.Column('description', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('announcement_drafts', 'description')
