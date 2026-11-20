"""catalogue documents paragraphs nullable premium

Revision ID: bc998d3223a3
Revises: a46cf99e02d4
Create Date: 2026-11-20 21:40:49.963801

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'bc998d3223a3'
down_revision = 'a46cf99e02d4'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('documents',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('doc_id', sa.String(length=120), nullable=False),
    sa.Column('type', sa.String(length=60), nullable=False),
    sa.Column('url', sa.String(length=600), nullable=True),
    sa.Column('language', sa.String(length=10), nullable=True),
    sa.Column('version', sa.String(length=200), nullable=True),
    sa.Column('page_count', sa.Integer(), nullable=True),
    sa.Column('sha256', sa.String(length=64), nullable=True),
    sa.Column('local_path', sa.String(length=300), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('doc_id')
    )
    op.create_table('document_versions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('document_id', sa.Integer(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('version', sa.String(length=200), nullable=True),
    sa.Column('page_count', sa.Integer(), nullable=False),
    sa.Column('ingested_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('document_id', 'sha256', name='uq_docversion_document_sha')
    )
    op.create_table('plan_documents',
    sa.Column('policy_id', sa.Integer(), nullable=False),
    sa.Column('document_id', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['policy_id'], ['policies.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('policy_id', 'document_id')
    )
    op.create_table('paragraphs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('document_version_id', sa.Integer(), nullable=False),
    sa.Column('page', sa.Integer(), nullable=False),
    sa.Column('ordinal', sa.Integer(), nullable=False),
    sa.Column('x0', sa.Float(), nullable=False),
    sa.Column('y0', sa.Float(), nullable=False),
    sa.Column('x1', sa.Float(), nullable=False),
    sa.Column('y1', sa.Float(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('paragraph_sha256', sa.String(length=64), nullable=False),
    sa.Column('clause_number', sa.String(length=30), nullable=True),
    sa.Column('heading', sa.String(length=200), nullable=True),
    sa.Column('scope', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['document_version_id'], ['document_versions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('document_version_id', 'page', 'ordinal', name='uq_paragraph_position')
    )
    with op.batch_alter_table('paragraphs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_paragraphs_document_version_id'), ['document_version_id'], unique=False)

    with op.batch_alter_table('policies', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_hidden', sa.Boolean(), server_default=sa.text('false'), nullable=False))
        batch_op.add_column(sa.Column('slug', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('kind', sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column('categories', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('catalogue_status', sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column('coverage_focus', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('contract_status', sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column('product_url', sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column('applicable_base_plans', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('catalogue_as_of', sa.Date(), nullable=True))
        batch_op.alter_column('monthly_premium_cents',
               existing_type=sa.INTEGER(),
               nullable=True)
        batch_op.create_unique_constraint('policies_slug_key', ['slug'])

    # The fictional demo plans leave the user-facing lists. They are hidden, not deleted,
    # so saved scenarios and comparison flags keep working.
    op.execute("UPDATE policies SET is_hidden = true WHERE is_demo = true")


def downgrade():
    # Imported catalogue plans have no premium, so they cannot go back to a NOT NULL column.
    # Only those rows (slug set) are removed; demo plans, users and answers are untouched.
    op.execute("DELETE FROM policies WHERE slug IS NOT NULL")
    with op.batch_alter_table('policies', schema=None) as batch_op:
        batch_op.drop_constraint('policies_slug_key', type_='unique')
        batch_op.alter_column('monthly_premium_cents',
               existing_type=sa.INTEGER(),
               nullable=False)
        batch_op.drop_column('catalogue_as_of')
        batch_op.drop_column('applicable_base_plans')
        batch_op.drop_column('product_url')
        batch_op.drop_column('contract_status')
        batch_op.drop_column('coverage_focus')
        batch_op.drop_column('catalogue_status')
        batch_op.drop_column('categories')
        batch_op.drop_column('kind')
        batch_op.drop_column('slug')
        batch_op.drop_column('is_hidden')

    with op.batch_alter_table('paragraphs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_paragraphs_document_version_id'))

    op.drop_table('paragraphs')
    op.drop_table('plan_documents')
    op.drop_table('document_versions')
    op.drop_table('documents')
