"""leads table (Phase 8.4)

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-01
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('leads',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('domain_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('crawl_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('website', sa.Text(), nullable=False),
    sa.Column('business_name', sa.Text(), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('emails', sa.JSON(), nullable=False),
    sa.Column('phones', sa.JSON(), nullable=False),
    sa.Column('address', sa.JSON(), nullable=True),
    sa.Column('social_profiles', sa.JSON(), nullable=False),
    sa.Column('page_type', sa.String(length=32), nullable=True),
    sa.Column('language', sa.String(length=32), nullable=True),
    sa.Column('source_url', sa.Text(), nullable=True),
    sa.Column('first_seen', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_seen', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint('first_seen IS NULL OR last_seen IS NULL OR last_seen >= first_seen', name=op.f('ck_leads_last_seen_after_first_seen')),
    sa.CheckConstraint('length(website) > 0', name=op.f('ck_leads_website_not_empty')),
    sa.ForeignKeyConstraint(['crawl_id'], ['crawls.id'], name=op.f('fk_leads_crawl_id_crawls'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['domain_id'], ['domains.id'], name=op.f('fk_leads_domain_id_domains'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_leads')),
    sa.UniqueConstraint('domain_id', name=op.f('uq_leads_domain_id'))
    )
    with op.batch_alter_table('leads', schema=None) as batch_op:
        batch_op.create_index('ix_leads_business_name', ['business_name'], unique=False)
        batch_op.create_index('ix_leads_crawl_id', ['crawl_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('leads', schema=None) as batch_op:
        batch_op.drop_index('ix_leads_crawl_id')
        batch_op.drop_index('ix_leads_business_name')

    op.drop_table('leads')
