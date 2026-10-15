"""add channel_attachments

Revision ID: e1b4d7a93c6f
Revises: c9f17a2e5db4
Create Date: 2026-10-15 02:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import reviewpulse.db.models  # custom column types (UtcDateTime)


# revision identifiers, used by Alembic.
revision: str = 'e1b4d7a93c6f'
down_revision: Union[str, Sequence[str], None] = 'c9f17a2e5db4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'channel_attachments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('channel_chat_id', sa.BigInteger(), nullable=False),
        sa.Column('post_message_id', sa.Integer(), nullable=False),
        sa.Column('message_id', sa.Integer(), nullable=False),
        sa.Column('created_at', reviewpulse.db.models.UtcDateTime(timezone=True), nullable=False),
        sa.Column('updated_at', reviewpulse.db.models.UtcDateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_channel_attachments_channel_chat_id'),
        'channel_attachments',
        ['channel_chat_id'],
    )
    op.create_index(
        op.f('ix_channel_attachments_post_message_id'),
        'channel_attachments',
        ['post_message_id'],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_channel_attachments_post_message_id'), table_name='channel_attachments')
    op.drop_index(op.f('ix_channel_attachments_channel_chat_id'), table_name='channel_attachments')
    op.drop_table('channel_attachments')
