"""crawl stop reason: cancelled

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-01
"""
from collections.abc import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "stop_reason IN ('completed', 'max_pages', 'max_crawl_time', 'seed_rejected')"
_NEW = "stop_reason IN ('completed', 'max_pages', 'max_crawl_time', 'seed_rejected', 'cancelled')"


def upgrade() -> None:
    with op.batch_alter_table('crawls', schema=None) as batch_op:
        batch_op.drop_constraint(op.f('ck_crawls_crawlstopreason'), type_='check')
        batch_op.create_check_constraint(op.f('ck_crawls_crawlstopreason'), _NEW)


def downgrade() -> None:
    op.execute("UPDATE crawls SET stop_reason = NULL WHERE stop_reason = 'cancelled'")
    with op.batch_alter_table('crawls', schema=None) as batch_op:
        batch_op.drop_constraint(op.f('ck_crawls_crawlstopreason'), type_='check')
        batch_op.create_check_constraint(op.f('ck_crawls_crawlstopreason'), _OLD)
