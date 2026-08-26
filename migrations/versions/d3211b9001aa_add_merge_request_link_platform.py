"""add merge_request_link platform

Revision ID: d3211b9001aa
Revises: 4c812a0a28a7
Create Date: 2026-08-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd3211b9001aa'
down_revision: Union[str, Sequence[str], None] = '4c812a0a28a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'merge_request_links',
        sa.Column(
            'platform', sa.String(length=16), nullable=False, server_default='gitlab'
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('merge_request_links', 'platform')
