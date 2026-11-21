"""week 7 readiness: FC gap-analysis findings and meeting audio retention

Additive only. Creates the gap_findings table (one FC decision per client finding) and adds the audio-retention
columns to meeting_recordings. Existing data is untouched: raw audio becomes nullable (it is deleted after a
successful transcription or when a failed segment expires), audio_deleted_at records when the bytes were removed,
and expires_at marks when un-transcribed audio may be deleted.

Revision ID: c4a1f09b7e22
Revises: 31a360607821
Create Date: 2026-11-21 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c4a1f09b7e22'
down_revision = '31a360607821'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'gap_findings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('fc_id', sa.Integer(), nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False),
        sa.Column('finding_key', sa.String(length=60), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=10), server_default='open', nullable=False),
        sa.Column('note', sa.String(length=600), nullable=True),
        sa.Column('source_hash', sa.String(length=64), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['fc_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['client_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('fc_id', 'client_id', 'finding_key', name='uq_gap_finding'),
        sa.CheckConstraint("status IN ('open', 'confirmed', 'dismissed')", name='ck_gap_finding_status'),
        sa.CheckConstraint("kind IN ('cover', 'overlap')", name='ck_gap_finding_kind'),
    )
    op.create_index(op.f('ix_gap_findings_fc_id'), 'gap_findings', ['fc_id'], unique=False)
    op.create_index(op.f('ix_gap_findings_client_id'), 'gap_findings', ['client_id'], unique=False)

    op.alter_column('meeting_recordings', 'audio', existing_type=sa.LargeBinary(), nullable=True)
    op.add_column('meeting_recordings', sa.Column('audio_deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('meeting_recordings', sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column('meeting_recordings', 'expires_at')
    op.drop_column('meeting_recordings', 'audio_deleted_at')
    op.alter_column('meeting_recordings', 'audio', existing_type=sa.LargeBinary(), nullable=False)
    op.drop_index(op.f('ix_gap_findings_client_id'), table_name='gap_findings')
    op.drop_index(op.f('ix_gap_findings_fc_id'), table_name='gap_findings')
    op.drop_table('gap_findings')
