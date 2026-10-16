"""add reviewer_pool_members.product, drop its username uniqueness

Revision ID: f4a8c2d61b93
Revises: e1b4d7a93c6f
Create Date: 2026-10-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4a8c2d61b93'
down_revision: Union[str, Sequence[str], None] = 'e1b4d7a93c6f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('reviewer_pool_members', schema=None) as batch_op:
        batch_op.add_column(sa.Column('product', sa.String(length=256), nullable=True))
        batch_op.drop_index('ix_reviewer_pool_members_username')
        batch_op.create_index(
            op.f('ix_reviewer_pool_members_username'), ['username'], unique=False
        )
        batch_op.create_index(
            op.f('ix_reviewer_pool_members_product'), ['product'], unique=False
        )
    # Every existing row keeps applying to every product (`product` left `NULL`) —
    # exactly today's one-shared-pool behavior, no data to migrate.


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('reviewer_pool_members', schema=None) as batch_op:
        batch_op.drop_index(op.f('ix_reviewer_pool_members_product'))
        batch_op.drop_index(op.f('ix_reviewer_pool_members_username'))
        batch_op.create_index(
            'ix_reviewer_pool_members_username', ['username'], unique=True
        )
        batch_op.drop_column('product')
