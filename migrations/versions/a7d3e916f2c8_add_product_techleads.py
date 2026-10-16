"""add product_techleads

Revision ID: a7d3e916f2c8
Revises: f4a8c2d61b93
Create Date: 2026-10-16 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import reviewpulse.db.models  # custom column types (UtcDateTime)


# revision identifiers, used by Alembic.
revision: str = 'a7d3e916f2c8'
down_revision: Union[str, Sequence[str], None] = 'f4a8c2d61b93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'product_techleads',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('product', sa.String(length=256), nullable=False),
        sa.Column('username', sa.String(length=64), nullable=True),
        sa.Column('set_by_user_id', sa.BigInteger(), nullable=True),
        sa.Column('created_at', reviewpulse.db.models.UtcDateTime(timezone=True), nullable=False),
        sa.Column('updated_at', reviewpulse.db.models.UtcDateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_product_techleads_product'), 'product_techleads', ['product'], unique=True
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_product_techleads_product'), table_name='product_techleads')
    op.drop_table('product_techleads')
