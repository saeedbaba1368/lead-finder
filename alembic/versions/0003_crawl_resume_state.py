"""crawl resume state

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-30
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('crawls', schema=None) as batch_op:
        batch_op.add_column(sa.Column('max_crawl_time', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('request_delay', sa.Float(), server_default='0.0', nullable=False))
        batch_op.add_column(sa.Column('current_depth', sa.Integer(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('elapsed_seconds', sa.Float(), server_default='0.0', nullable=False))
        batch_op.add_column(sa.Column('stop_reason', sa.Enum('completed', 'max_pages', 'max_crawl_time', 'seed_rejected', name='crawlstopreason', native_enum=False, create_constraint=False, length=32), nullable=True))
        batch_op.add_column(sa.Column('last_checkpoint_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.create_check_constraint(op.f('ck_crawls_crawlstopreason'), "stop_reason IN ('completed', 'max_pages', 'max_crawl_time', 'seed_rejected')")
        batch_op.create_check_constraint(op.f('ck_crawls_current_depth_non_negative'), 'current_depth >= 0')
        batch_op.create_check_constraint(op.f('ck_crawls_elapsed_seconds_non_negative'), 'elapsed_seconds >= 0')
        batch_op.create_check_constraint(op.f('ck_crawls_max_crawl_time_positive'), 'max_crawl_time IS NULL OR max_crawl_time > 0')
        batch_op.create_check_constraint(op.f('ck_crawls_request_delay_non_negative'), 'request_delay >= 0')

    with op.batch_alter_table('pages', schema=None) as batch_op:
        batch_op.add_column(sa.Column('parent_url', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('pages', schema=None) as batch_op:
        batch_op.drop_column('parent_url')

    with op.batch_alter_table('crawls', schema=None) as batch_op:
        batch_op.drop_constraint(op.f('ck_crawls_request_delay_non_negative'), type_='check')
        batch_op.drop_constraint(op.f('ck_crawls_max_crawl_time_positive'), type_='check')
        batch_op.drop_constraint(op.f('ck_crawls_elapsed_seconds_non_negative'), type_='check')
        batch_op.drop_constraint(op.f('ck_crawls_current_depth_non_negative'), type_='check')
        batch_op.drop_constraint(op.f('ck_crawls_crawlstopreason'), type_='check')
        batch_op.drop_column('last_checkpoint_at')
        batch_op.drop_column('stop_reason')
        batch_op.drop_column('elapsed_seconds')
        batch_op.drop_column('current_depth')
        batch_op.drop_column('request_delay')
        batch_op.drop_column('max_crawl_time')
