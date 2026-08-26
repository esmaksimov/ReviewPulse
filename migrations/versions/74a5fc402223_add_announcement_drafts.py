"""add announcement_drafts

Revision ID: 74a5fc402223
Revises: 2a361b3cc0d8
Create Date: 2026-08-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import reviewpulse.db.models  # custom column types (UtcDateTime)


# revision identifiers, used by Alembic.
revision: str = '74a5fc402223'
down_revision: Union[str, Sequence[str], None] = '2a361b3cc0d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'announcement_drafts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('composer_user_id', sa.BigInteger(), nullable=False),
        sa.Column('composer_username', sa.String(length=64), nullable=False),
        sa.Column('chat_id', sa.BigInteger(), nullable=False),
        sa.Column('preview_message_id', sa.Integer(), nullable=True),
        sa.Column('project_path', sa.String(length=512), nullable=False),
        sa.Column('product', sa.String(length=256), nullable=False),
        sa.Column('title', sa.String(length=512), nullable=True),
        sa.Column('task_url', sa.Text(), nullable=True),
        sa.Column('docs_url', sa.Text(), nullable=True),
        sa.Column('merge_requests_json', sa.Text(), nullable=False),
        sa.Column('techlead_username', sa.String(length=64), nullable=True),
        sa.Column('pool_pick_usernames_json', sa.Text(), nullable=False),
        sa.Column('published_at', reviewpulse.db.models.UtcDateTime(timezone=True), nullable=True),
        sa.Column('cancelled_at', reviewpulse.db.models.UtcDateTime(timezone=True), nullable=True),
        sa.Column('created_at', reviewpulse.db.models.UtcDateTime(timezone=True), nullable=False),
        sa.Column('updated_at', reviewpulse.db.models.UtcDateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_announcement_drafts_composer_user_id'),
        'announcement_drafts',
        ['composer_user_id'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f('ix_announcement_drafts_composer_user_id'), table_name='announcement_drafts'
    )
    op.drop_table('announcement_drafts')
