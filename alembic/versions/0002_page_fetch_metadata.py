"""page fetch metadata

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-30
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('pages', schema=None) as batch_op:
        batch_op.add_column(sa.Column('final_url', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('charset', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('response_size', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True))
        batch_op.add_column(sa.Column('duration_seconds', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('redirect_count', sa.Integer(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('redirect_chain', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('outcome', sa.Enum('ok', 'http_error', 'redirect_not_followed', 'redirect_error', 'unsupported_content_type', 'too_large', 'timeout', 'connection_error', 'invalid_url', 'blocked', 'error', name='pageoutcome', native_enum=False, create_constraint=False, length=32), nullable=True))
        batch_op.add_column(sa.Column('dedupe_key', sa.String(length=64), nullable=True))
        batch_op.create_check_constraint(op.f('ck_pages_pageoutcome'), "outcome IN ('ok', 'http_error', 'redirect_not_followed', 'redirect_error', 'unsupported_content_type', 'too_large', 'timeout', 'connection_error', 'invalid_url', 'blocked', 'error')")
        batch_op.create_check_constraint(op.f('ck_pages_response_size_non_negative'), 'response_size IS NULL OR response_size >= 0')
        batch_op.create_check_constraint(op.f('ck_pages_duration_non_negative'), 'duration_seconds IS NULL OR duration_seconds >= 0')
        batch_op.create_check_constraint(op.f('ck_pages_redirect_count_non_negative'), 'redirect_count >= 0')
        batch_op.create_index('ix_pages_dedupe_key', ['dedupe_key'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('pages', schema=None) as batch_op:
        batch_op.drop_index('ix_pages_dedupe_key')
        batch_op.drop_constraint(op.f('ck_pages_redirect_count_non_negative'), type_='check')
        batch_op.drop_constraint(op.f('ck_pages_duration_non_negative'), type_='check')
        batch_op.drop_constraint(op.f('ck_pages_response_size_non_negative'), type_='check')
        batch_op.drop_constraint(op.f('ck_pages_pageoutcome'), type_='check')
        batch_op.drop_column('dedupe_key')
        batch_op.drop_column('outcome')
        batch_op.drop_column('redirect_chain')
        batch_op.drop_column('redirect_count')
        batch_op.drop_column('duration_seconds')
        batch_op.drop_column('response_size')
        batch_op.drop_column('charset')
        batch_op.drop_column('final_url')
