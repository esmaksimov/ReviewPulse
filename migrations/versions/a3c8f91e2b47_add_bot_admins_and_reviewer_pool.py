"""add bot_admins, reviewer_pool_members, draft attachments

Revision ID: a3c8f91e2b47
Revises: f2a9c6d4e871
Create Date: 2026-10-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import reviewpulse.db.models  # custom column types (UtcDateTime)


# revision identifiers, used by Alembic.
revision: str = 'a3c8f91e2b47'
down_revision: Union[str, Sequence[str], None] = 'f2a9c6d4e871'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'bot_admins',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('telegram_user_id', sa.BigInteger(), nullable=False),
        sa.Column('username', sa.String(length=64), nullable=True),
        sa.Column('added_by_user_id', sa.BigInteger(), nullable=True),
        sa.Column('created_at', reviewpulse.db.models.UtcDateTime(timezone=True), nullable=False),
        sa.Column('updated_at', reviewpulse.db.models.UtcDateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_bot_admins_telegram_user_id'), 'bot_admins', ['telegram_user_id'], unique=True
    )

    op.create_table(
        'reviewer_pool_members',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(length=64), nullable=False),
        sa.Column('added_by_user_id', sa.BigInteger(), nullable=True),
        sa.Column('created_at', reviewpulse.db.models.UtcDateTime(timezone=True), nullable=False),
        sa.Column('updated_at', reviewpulse.db.models.UtcDateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_reviewer_pool_members_username'), 'reviewer_pool_members', ['username'],
        unique=True,
    )

    op.add_column(
        'announcement_drafts',
        sa.Column('attachments_json', sa.Text(), nullable=False, server_default='[]'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('announcement_drafts', 'attachments_json')
    op.drop_index(op.f('ix_reviewer_pool_members_username'), table_name='reviewer_pool_members')
    op.drop_table('reviewer_pool_members')
    op.drop_index(op.f('ix_bot_admins_telegram_user_id'), table_name='bot_admins')
    op.drop_table('bot_admins')
