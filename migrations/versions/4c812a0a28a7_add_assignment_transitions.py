"""add assignment_transitions

Revision ID: 4c812a0a28a7
Revises: 74a5fc402223
Create Date: 2026-08-26 00:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import reviewpulse.db.models  # custom column types (UtcDateTime)


# revision identifiers, used by Alembic.
revision: str = '4c812a0a28a7'
down_revision: Union[str, Sequence[str], None] = '74a5fc402223'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_REVIEWER_STATE = sa.Enum(
    'pending', 'changes_requested', 'awaiting_recheck', 'approved',
    name='reviewer_state', native_enum=False, length=32,
)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'assignment_transitions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('assignment_id', sa.Integer(), nullable=False),
        sa.Column('review_id', sa.Integer(), nullable=False),
        sa.Column('from_state', _REVIEWER_STATE, nullable=False),
        sa.Column('to_state', _REVIEWER_STATE, nullable=False),
        sa.Column('event', sa.String(length=32), nullable=False),
        sa.Column('at', reviewpulse.db.models.UtcDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['assignment_id'], ['reviewer_assignments.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['review_id'], ['reviews.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_assignment_transitions_assignment_id'),
        'assignment_transitions', ['assignment_id'], unique=False,
    )
    op.create_index(
        op.f('ix_assignment_transitions_review_id'),
        'assignment_transitions', ['review_id'], unique=False,
    )
    op.create_index(
        op.f('ix_assignment_transitions_at'), 'assignment_transitions', ['at'], unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_assignment_transitions_at'), table_name='assignment_transitions')
    op.drop_index(op.f('ix_assignment_transitions_review_id'), table_name='assignment_transitions')
    op.drop_index(op.f('ix_assignment_transitions_assignment_id'), table_name='assignment_transitions')
    op.drop_table('assignment_transitions')
