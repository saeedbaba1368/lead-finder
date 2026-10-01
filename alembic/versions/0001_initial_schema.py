"""initial schema

Revision ID: 0001
Revises: (none)
Create Date: 2026-09-30
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('api_keys',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('key_prefix', sa.String(length=16), nullable=False),
    sa.Column('key_hash', sa.String(length=128), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default=sa.true(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_api_keys')),
    sa.UniqueConstraint('key_hash', name=op.f('uq_api_keys_key_hash')),
    sa.UniqueConstraint('key_prefix', name=op.f('uq_api_keys_key_prefix'))
    )
    op.create_table('domains',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('name', sa.String(length=253), nullable=False),
    sa.Column('last_crawled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint('length(name) > 0', name=op.f('ck_domains_name_not_empty')),
    sa.CheckConstraint('name = lower(name)', name=op.f('ck_domains_name_lowercase')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_domains')),
    sa.UniqueConstraint('name', name=op.f('uq_domains_name'))
    )
    op.create_table('crawls',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('domain_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('api_key_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('status', sa.Enum('pending', 'running', 'completed', 'failed', 'cancelled', name='crawlstatus', native_enum=False, create_constraint=False, length=32), server_default='pending', nullable=False),
    sa.Column('seed_url', sa.Text(), nullable=True),
    sa.Column('max_pages', sa.Integer(), server_default='100', nullable=False),
    sa.Column('max_depth', sa.Integer(), server_default='2', nullable=False),
    sa.Column('pages_crawled', sa.Integer(), server_default='0', nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("status IN ('pending', 'running', 'completed', 'failed', 'cancelled')", name=op.f('ck_crawls_crawlstatus')),
    sa.CheckConstraint('max_depth >= 0', name=op.f('ck_crawls_max_depth_non_negative')),
    sa.CheckConstraint('max_pages > 0', name=op.f('ck_crawls_max_pages_positive')),
    sa.CheckConstraint('pages_crawled >= 0', name=op.f('ck_crawls_pages_crawled_non_negative')),
    sa.CheckConstraint('started_at IS NULL OR finished_at IS NULL OR finished_at >= started_at', name=op.f('ck_crawls_finished_after_started')),
    sa.ForeignKeyConstraint(['api_key_id'], ['api_keys.id'], name=op.f('fk_crawls_api_key_id_api_keys'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['domain_id'], ['domains.id'], name=op.f('fk_crawls_domain_id_domains'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_crawls'))
    )
    with op.batch_alter_table('crawls', schema=None) as batch_op:
        batch_op.create_index('ix_crawls_api_key_id', ['api_key_id'], unique=False)
        batch_op.create_index('ix_crawls_domain_id_status', ['domain_id', 'status'], unique=False)
        batch_op.create_index('ix_crawls_status_created_at', ['status', 'created_at'], unique=False)

    op.create_table('email_patterns',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('domain_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('pattern', sa.Enum('first', 'last', 'first.last', 'firstlast', 'f.last', 'flast', 'first.l', 'firstl', 'last.first', 'first_last', 'other', name='emailpatterntype', native_enum=False, create_constraint=False, length=32), nullable=False),
    sa.Column('sample_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('confidence', sa.Float(), server_default='0.0', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("pattern IN ('first', 'last', 'first.last', 'firstlast', 'f.last', 'flast', 'first.l', 'firstl', 'last.first', 'first_last', 'other')", name=op.f('ck_email_patterns_emailpatterntype')),
    sa.CheckConstraint('confidence >= 0 AND confidence <= 1', name=op.f('ck_email_patterns_confidence_range')),
    sa.CheckConstraint('sample_count >= 0', name=op.f('ck_email_patterns_sample_count_non_negative')),
    sa.ForeignKeyConstraint(['domain_id'], ['domains.id'], name=op.f('fk_email_patterns_domain_id_domains'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_email_patterns')),
    sa.UniqueConstraint('domain_id', 'pattern', name=op.f('uq_email_patterns_domain_id'))
    )
    op.create_table('emails',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('domain_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('address', sa.String(length=320), nullable=False),
    sa.Column('local_part', sa.String(length=64), nullable=False),
    sa.Column('is_role_based', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("address LIKE '%_@_%'", name=op.f('ck_emails_address_format')),
    sa.CheckConstraint('address = lower(address)', name=op.f('ck_emails_address_lowercase')),
    sa.ForeignKeyConstraint(['domain_id'], ['domains.id'], name=op.f('fk_emails_domain_id_domains'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_emails')),
    sa.UniqueConstraint('domain_id', 'address', name=op.f('uq_emails_domain_id'))
    )
    with op.batch_alter_table('emails', schema=None) as batch_op:
        batch_op.create_index('ix_emails_address', ['address'], unique=False)

    op.create_table('email_verifications',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('email_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('status', sa.Enum('unknown', 'valid', 'invalid', 'risky', 'catch_all', name='verificationstatus', native_enum=False, create_constraint=False, length=32), server_default='unknown', nullable=False),
    sa.Column('method', sa.Enum('syntax', 'dns_mx', 'smtp', 'third_party', name='verificationmethod', native_enum=False, create_constraint=False, length=32), nullable=False),
    sa.Column('reason', sa.String(length=255), nullable=True),
    sa.Column('mx_host', sa.String(length=255), nullable=True),
    sa.Column('smtp_code', sa.Integer(), nullable=True),
    sa.Column('is_catch_all', sa.Boolean(), nullable=True),
    sa.Column('provider', sa.String(length=64), nullable=True),
    sa.Column('checked_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("method IN ('syntax', 'dns_mx', 'smtp', 'third_party')", name=op.f('ck_email_verifications_verificationmethod')),
    sa.CheckConstraint("status IN ('unknown', 'valid', 'invalid', 'risky', 'catch_all')", name=op.f('ck_email_verifications_verificationstatus')),
    sa.CheckConstraint('smtp_code IS NULL OR (smtp_code >= 100 AND smtp_code <= 599)', name=op.f('ck_email_verifications_smtp_code_range')),
    sa.ForeignKeyConstraint(['email_id'], ['emails.id'], name=op.f('fk_email_verifications_email_id_emails'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_email_verifications'))
    )
    with op.batch_alter_table('email_verifications', schema=None) as batch_op:
        batch_op.create_index('ix_email_verifications_email_id_checked_at', ['email_id', 'checked_at'], unique=False)
        batch_op.create_index('ix_email_verifications_status', ['status'], unique=False)

    op.create_table('pages',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('crawl_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('url_hash', sa.String(length=64), nullable=False),
    sa.Column('depth', sa.Integer(), server_default='0', nullable=False),
    sa.Column('status', sa.Enum('pending', 'fetched', 'failed', 'skipped', name='pagestatus', native_enum=False, create_constraint=False, length=32), server_default='pending', nullable=False),
    sa.Column('http_status', sa.Integer(), nullable=True),
    sa.Column('content_type', sa.String(length=255), nullable=True),
    sa.Column('content_hash', sa.String(length=64), nullable=True),
    sa.Column('title', sa.Text(), nullable=True),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("status IN ('pending', 'fetched', 'failed', 'skipped')", name=op.f('ck_pages_pagestatus')),
    sa.CheckConstraint('depth >= 0', name=op.f('ck_pages_depth_non_negative')),
    sa.CheckConstraint('http_status IS NULL OR (http_status >= 100 AND http_status <= 599)', name=op.f('ck_pages_http_status_range')),
    sa.ForeignKeyConstraint(['crawl_id'], ['crawls.id'], name=op.f('fk_pages_crawl_id_crawls'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_pages')),
    sa.UniqueConstraint('crawl_id', 'url_hash', name=op.f('uq_pages_crawl_id'))
    )
    with op.batch_alter_table('pages', schema=None) as batch_op:
        batch_op.create_index('ix_pages_content_hash', ['content_hash'], unique=False)
        batch_op.create_index('ix_pages_crawl_id_status', ['crawl_id', 'status'], unique=False)

    op.create_table('people',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('domain_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('email_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('full_name', sa.String(length=255), nullable=False),
    sa.Column('first_name', sa.String(length=128), nullable=True),
    sa.Column('last_name', sa.String(length=128), nullable=True),
    sa.Column('job_title', sa.String(length=255), nullable=True),
    sa.Column('confidence', sa.Float(), server_default='0.0', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint('confidence >= 0 AND confidence <= 1', name=op.f('ck_people_confidence_range')),
    sa.CheckConstraint('length(full_name) > 0', name=op.f('ck_people_full_name_not_empty')),
    sa.ForeignKeyConstraint(['domain_id'], ['domains.id'], name=op.f('fk_people_domain_id_domains'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['email_id'], ['emails.id'], name=op.f('fk_people_email_id_emails'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_people'))
    )
    with op.batch_alter_table('people', schema=None) as batch_op:
        batch_op.create_index('ix_people_domain_id_last_first', ['domain_id', 'last_name', 'first_name'], unique=False)
        batch_op.create_index('ix_people_email_id', ['email_id'], unique=False)

    op.create_table('email_evidence',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('email_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('page_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('source_type', sa.Enum('mailto_link', 'page_text', 'obfuscated_text', 'structured_data', 'other', name='emailevidencesource', native_enum=False, create_constraint=False, length=32), nullable=False),
    sa.Column('snippet', sa.Text(), nullable=True),
    sa.Column('confidence', sa.Float(), server_default='1.0', nullable=False),
    sa.Column('occurrences', sa.Integer(), server_default='1', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("source_type IN ('mailto_link', 'page_text', 'obfuscated_text', 'structured_data', 'other')", name=op.f('ck_email_evidence_emailevidencesource')),
    sa.CheckConstraint('confidence >= 0 AND confidence <= 1', name=op.f('ck_email_evidence_confidence_range')),
    sa.CheckConstraint('occurrences >= 1', name=op.f('ck_email_evidence_occurrences_positive')),
    sa.ForeignKeyConstraint(['email_id'], ['emails.id'], name=op.f('fk_email_evidence_email_id_emails'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['page_id'], ['pages.id'], name=op.f('fk_email_evidence_page_id_pages'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_email_evidence')),
    sa.UniqueConstraint('email_id', 'page_id', 'source_type', name=op.f('uq_email_evidence_email_id'))
    )
    with op.batch_alter_table('email_evidence', schema=None) as batch_op:
        batch_op.create_index('ix_email_evidence_page_id', ['page_id'], unique=False)

    op.create_table('person_evidence',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('person_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('page_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('source_type', sa.Enum('team_listing', 'byline', 'structured_data', 'page_text', 'other', name='personevidencesource', native_enum=False, create_constraint=False, length=32), nullable=False),
    sa.Column('snippet', sa.Text(), nullable=True),
    sa.Column('confidence', sa.Float(), server_default='1.0', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("source_type IN ('team_listing', 'byline', 'structured_data', 'page_text', 'other')", name=op.f('ck_person_evidence_personevidencesource')),
    sa.CheckConstraint('confidence >= 0 AND confidence <= 1', name=op.f('ck_person_evidence_confidence_range')),
    sa.ForeignKeyConstraint(['page_id'], ['pages.id'], name=op.f('fk_person_evidence_page_id_pages'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['person_id'], ['people.id'], name=op.f('fk_person_evidence_person_id_people'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_person_evidence')),
    sa.UniqueConstraint('person_id', 'page_id', 'source_type', name=op.f('uq_person_evidence_person_id'))
    )
    with op.batch_alter_table('person_evidence', schema=None) as batch_op:
        batch_op.create_index('ix_person_evidence_page_id', ['page_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('person_evidence', schema=None) as batch_op:
        batch_op.drop_index('ix_person_evidence_page_id')

    op.drop_table('person_evidence')
    with op.batch_alter_table('email_evidence', schema=None) as batch_op:
        batch_op.drop_index('ix_email_evidence_page_id')

    op.drop_table('email_evidence')
    with op.batch_alter_table('people', schema=None) as batch_op:
        batch_op.drop_index('ix_people_email_id')
        batch_op.drop_index('ix_people_domain_id_last_first')

    op.drop_table('people')
    with op.batch_alter_table('pages', schema=None) as batch_op:
        batch_op.drop_index('ix_pages_crawl_id_status')
        batch_op.drop_index('ix_pages_content_hash')

    op.drop_table('pages')
    with op.batch_alter_table('email_verifications', schema=None) as batch_op:
        batch_op.drop_index('ix_email_verifications_status')
        batch_op.drop_index('ix_email_verifications_email_id_checked_at')

    op.drop_table('email_verifications')
    with op.batch_alter_table('emails', schema=None) as batch_op:
        batch_op.drop_index('ix_emails_address')

    op.drop_table('emails')
    op.drop_table('email_patterns')
    with op.batch_alter_table('crawls', schema=None) as batch_op:
        batch_op.drop_index('ix_crawls_status_created_at')
        batch_op.drop_index('ix_crawls_domain_id_status')
        batch_op.drop_index('ix_crawls_api_key_id')

    op.drop_table('crawls')
    op.drop_table('domains')
    op.drop_table('api_keys')
