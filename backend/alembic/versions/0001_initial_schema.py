"""initial schema

Everything the build needs: identity, sources and chunks, jobs, claims with evidence and versions,
conflicts, interview questions, acknowledgements and the audit log.

Revision ID: 0001
Revises: 
Create Date: 2026-11-10 22:39:33.677538

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('projects',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('slug', sa.String(length=80), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('as_of', sa.Date(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_projects')),
    sa.UniqueConstraint('slug', name=op.f('uq_projects_slug'))
    )
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('role', sa.Enum('admin', 'manager', 'leaver', 'successor', 'teammate', name='role', native_enum=False, length=32), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.CheckConstraint('email = lower(email)', name=op.f('ck_users_email_lowercase')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('email', name=op.f('uq_users_email'))
    )
    op.create_table('acknowledgements',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('role', sa.Enum('admin', 'manager', 'leaver', 'successor', 'teammate', name='role', native_enum=False, length=32), nullable=False),
    sa.Column('view_sha256', sa.String(length=64), nullable=False),
    sa.Column('visible_count', sa.Integer(), nullable=False),
    sa.Column('withheld_count', sa.Integer(), nullable=False),
    sa.Column('acknowledged_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.CheckConstraint('visible_count >= 0 AND withheld_count >= 0', name=op.f('ck_acknowledgements_counts_not_negative')),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_acknowledgements_project_id_projects'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_acknowledgements_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_acknowledgements')),
    sa.UniqueConstraint('user_id', 'project_id', 'role', 'view_sha256', name='uq_acknowledgements_view')
    )
    op.create_index(op.f('ix_acknowledgements_project_id'), 'acknowledgements', ['project_id'], unique=False)
    op.create_index(op.f('ix_acknowledgements_user_id'), 'acknowledgements', ['user_id'], unique=False)
    op.create_table('audit_log',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=True),
    sa.Column('actor_id', sa.Integer(), nullable=True),
    sa.Column('actor_label', sa.String(length=255), nullable=False),
    sa.Column('action', sa.String(length=80), nullable=False),
    sa.Column('target_type', sa.String(length=40), nullable=True),
    sa.Column('target_id', sa.String(length=64), nullable=True),
    sa.Column('detail', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], name=op.f('fk_audit_log_actor_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_audit_log_project_id_projects'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_log'))
    )
    op.create_index('ix_audit_log_project_created', 'audit_log', ['project_id', 'created_at'], unique=False)
    op.create_index('ix_audit_log_target', 'audit_log', ['target_type', 'target_id'], unique=False)
    op.create_table('conflicts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('subject', sa.String(length=200), nullable=False),
    sa.Column('attribute', sa.String(length=200), nullable=False),
    sa.Column('status', sa.Enum('open', 'resolved', name='conflictstatus', native_enum=False, length=32), nullable=False),
    sa.Column('resolution_note', sa.Text(), nullable=True),
    sa.Column('resolved_by_id', sa.Integer(), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.CheckConstraint("status <> 'resolved' OR resolved_at IS NOT NULL", name=op.f('ck_conflicts_resolved_has_timestamp')),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_conflicts_project_id_projects'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['resolved_by_id'], ['users.id'], name=op.f('fk_conflicts_resolved_by_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_conflicts'))
    )
    op.create_index(op.f('ix_conflicts_project_id'), 'conflicts', ['project_id'], unique=False)
    op.create_table('jobs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('status', sa.Enum('queued', 'running', 'succeeded', 'failed', name='jobstatus', native_enum=False, length=32), nullable=False),
    sa.Column('stage', sa.String(length=60), nullable=True),
    sa.Column('error', sa.String(length=500), nullable=True),
    sa.Column('result', sa.JSON(), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status <> 'failed' OR error IS NOT NULL", name=op.f('ck_jobs_failed_has_error')),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_jobs_created_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_jobs_project_id_projects'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_jobs'))
    )
    op.create_index('ix_jobs_project_created', 'jobs', ['project_id', 'created_at'], unique=False)
    op.create_table('sources',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.Enum('github', 'notes', 'tasks', 'chat', 'leaver_qa', name='sourcekind', native_enum=False, length=32), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('clearance', sa.Integer(), server_default=sa.text('3'), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('content_sha256', sa.String(length=64), nullable=False),
    sa.Column('byte_size', sa.Integer(), nullable=False),
    sa.Column('redaction_counts', sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
    sa.Column('status', sa.Enum('ingested', 'failed', name='sourcestatus', native_enum=False, length=32), nullable=False),
    sa.Column('error', sa.String(length=500), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.CheckConstraint("status <> 'failed' OR error IS NOT NULL", name=op.f('ck_sources_failed_has_error')),
    sa.CheckConstraint("status = 'failed' OR length(text) > 0", name=op.f('ck_sources_ingested_has_text')),
    sa.CheckConstraint('clearance BETWEEN 0 AND 3', name=op.f('ck_sources_clearance_range')),
    sa.CheckConstraint('length(content_sha256) = 64', name=op.f('ck_sources_sha256_length')),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_sources_created_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_sources_project_id_projects'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sources')),
    sa.UniqueConstraint('project_id', 'content_sha256', name='uq_sources_project_content')
    )
    op.create_index(op.f('ix_sources_project_id'), 'sources', ['project_id'], unique=False)
    op.create_table('chunks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('ordinal', sa.Integer(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('locator', sa.String(length=255), nullable=True),
    sa.Column('author', sa.String(length=120), nullable=True),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('length(trim(text)) > 0', name=op.f('ck_chunks_text_not_blank')),
    sa.CheckConstraint('ordinal >= 0', name=op.f('ck_chunks_ordinal_not_negative')),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], name=op.f('fk_chunks_source_id_sources'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_chunks')),
    sa.UniqueConstraint('source_id', 'ordinal', name='uq_chunks_source_ordinal')
    )
    op.create_index(op.f('ix_chunks_source_id'), 'chunks', ['source_id'], unique=False)
    op.create_table('claims',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('job_id', sa.String(length=36), nullable=True),
    sa.Column('conflict_id', sa.Integer(), nullable=True),
    sa.Column('kind', sa.Enum('fact', 'decision', 'owner', name='claimkind', native_enum=False, length=32), nullable=False),
    sa.Column('subject', sa.String(length=200), nullable=False),
    sa.Column('attribute', sa.String(length=200), nullable=False),
    sa.Column('value', sa.Text(), nullable=True),
    sa.Column('statement', sa.Text(), nullable=False),
    sa.Column('label', sa.Enum('supported', 'single_source', 'conflicting', 'stale', 'missing', name='label', native_enum=False, length=32), nullable=False),
    sa.Column('review_status', sa.Enum('proposed', 'accepted', 'rejected', name='reviewstatus', native_enum=False, length=32), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.CheckConstraint('version >= 1', name=op.f('ck_claims_version_positive')),
    sa.ForeignKeyConstraint(['conflict_id'], ['conflicts.id'], name=op.f('fk_claims_conflict_id_conflicts'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_claims_job_id_jobs'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_claims_project_id_projects'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_claims'))
    )
    op.create_index(op.f('ix_claims_conflict_id'), 'claims', ['conflict_id'], unique=False)
    op.create_index(op.f('ix_claims_job_id'), 'claims', ['job_id'], unique=False)
    op.create_index('ix_claims_project_subject_attribute', 'claims', ['project_id', 'subject', 'attribute'], unique=False)
    op.create_table('claim_versions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('claim_id', sa.Integer(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('action', sa.Enum('created', 'edited', 'accepted', 'rejected', name='versionaction', native_enum=False, length=32), nullable=False),
    sa.Column('statement', sa.Text(), nullable=False),
    sa.Column('value', sa.Text(), nullable=True),
    sa.Column('review_status', sa.Enum('proposed', 'accepted', 'rejected', name='reviewstatus', native_enum=False, length=32), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('actor_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], name=op.f('fk_claim_versions_actor_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['claim_id'], ['claims.id'], name=op.f('fk_claim_versions_claim_id_claims'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_claim_versions')),
    sa.UniqueConstraint('claim_id', 'version', name='uq_claim_versions_claim_version')
    )
    op.create_index(op.f('ix_claim_versions_claim_id'), 'claim_versions', ['claim_id'], unique=False)
    op.create_table('evidence',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('claim_id', sa.Integer(), nullable=False),
    sa.Column('chunk_id', sa.Integer(), nullable=False),
    sa.Column('quote', sa.Text(), nullable=False),
    sa.Column('verified', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.CheckConstraint('length(trim(quote)) > 0', name=op.f('ck_evidence_quote_not_blank')),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.id'], name=op.f('fk_evidence_chunk_id_chunks')),
    sa.ForeignKeyConstraint(['claim_id'], ['claims.id'], name=op.f('fk_evidence_claim_id_claims'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_evidence'))
    )
    op.create_index(op.f('ix_evidence_chunk_id'), 'evidence', ['chunk_id'], unique=False)
    op.create_index(op.f('ix_evidence_claim_id'), 'evidence', ['claim_id'], unique=False)
    op.create_table('questions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('job_id', sa.String(length=36), nullable=True),
    sa.Column('claim_id', sa.Integer(), nullable=True),
    sa.Column('conflict_id', sa.Integer(), nullable=True),
    sa.Column('kind', sa.Enum('conflict', 'missing', 'stale', 'single_source', name='questionkind', native_enum=False, length=32), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('status', sa.Enum('open', 'answered', 'skipped', name='questionstatus', native_enum=False, length=32), nullable=False),
    sa.Column('answer', sa.Text(), nullable=True),
    sa.Column('answered_by_id', sa.Integer(), nullable=True),
    sa.Column('answered_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('answer_source_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.CheckConstraint("status <> 'answered' OR answer IS NOT NULL", name=op.f('ck_questions_answered_has_answer')),
    sa.CheckConstraint('claim_id IS NOT NULL OR conflict_id IS NOT NULL', name=op.f('ck_questions_has_target')),
    sa.ForeignKeyConstraint(['answer_source_id'], ['sources.id'], name=op.f('fk_questions_answer_source_id_sources'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['answered_by_id'], ['users.id'], name=op.f('fk_questions_answered_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['claim_id'], ['claims.id'], name=op.f('fk_questions_claim_id_claims'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['conflict_id'], ['conflicts.id'], name=op.f('fk_questions_conflict_id_conflicts'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_questions_job_id_jobs'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_questions_project_id_projects'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_questions'))
    )
    op.create_index(op.f('ix_questions_claim_id'), 'questions', ['claim_id'], unique=False)
    op.create_index(op.f('ix_questions_conflict_id'), 'questions', ['conflict_id'], unique=False)
    op.create_index(op.f('ix_questions_project_id'), 'questions', ['project_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_questions_project_id'), table_name='questions')
    op.drop_index(op.f('ix_questions_conflict_id'), table_name='questions')
    op.drop_index(op.f('ix_questions_claim_id'), table_name='questions')
    op.drop_table('questions')
    op.drop_index(op.f('ix_evidence_claim_id'), table_name='evidence')
    op.drop_index(op.f('ix_evidence_chunk_id'), table_name='evidence')
    op.drop_table('evidence')
    op.drop_index(op.f('ix_claim_versions_claim_id'), table_name='claim_versions')
    op.drop_table('claim_versions')
    op.drop_index('ix_claims_project_subject_attribute', table_name='claims')
    op.drop_index(op.f('ix_claims_job_id'), table_name='claims')
    op.drop_index(op.f('ix_claims_conflict_id'), table_name='claims')
    op.drop_table('claims')
    op.drop_index(op.f('ix_chunks_source_id'), table_name='chunks')
    op.drop_table('chunks')
    op.drop_index(op.f('ix_sources_project_id'), table_name='sources')
    op.drop_table('sources')
    op.drop_index('ix_jobs_project_created', table_name='jobs')
    op.drop_table('jobs')
    op.drop_index(op.f('ix_conflicts_project_id'), table_name='conflicts')
    op.drop_table('conflicts')
    op.drop_index('ix_audit_log_target', table_name='audit_log')
    op.drop_index('ix_audit_log_project_created', table_name='audit_log')
    op.drop_table('audit_log')
    op.drop_index(op.f('ix_acknowledgements_user_id'), table_name='acknowledgements')
    op.drop_index(op.f('ix_acknowledgements_project_id'), table_name='acknowledgements')
    op.drop_table('acknowledgements')
    op.drop_table('users')
    op.drop_table('projects')
