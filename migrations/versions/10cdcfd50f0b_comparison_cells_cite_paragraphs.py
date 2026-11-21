"""comparison cells cite paragraphs

Revision ID: 10cdcfd50f0b
Revises: bc998d3223a3
Create Date: 2026-11-21 10:02:23.089135

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '10cdcfd50f0b'
down_revision = 'bc998d3223a3'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('comparison_cells', schema=None) as batch_op:
        batch_op.add_column(sa.Column('paragraph_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('paragraphs_hash', sa.String(length=64), nullable=True))
        batch_op.create_foreign_key(
            'fk_comparison_cells_paragraph_id', 'paragraphs', ['paragraph_id'], ['id'], ondelete='SET NULL'
        )
        # "Not included" still needs a citation: a demo clause or a contract paragraph.
        # Every existing row satisfied the old rule, so it satisfies this wider one.
        batch_op.drop_constraint('ck_cell_not_included_cited', type_='check')
        batch_op.create_check_constraint(
            'ck_cell_not_included_cited',
            "state <> 'not_included' OR clause_id IS NOT NULL OR paragraph_id IS NOT NULL",
        )


def downgrade():
    # Cells that cite a contract paragraph were made by `flask generate-comparison`; drop them
    # (demo cells, users and flags are untouched).
    op.execute("DELETE FROM comparison_cells WHERE paragraph_id IS NOT NULL OR paragraphs_hash IS NOT NULL")
    with op.batch_alter_table('comparison_cells', schema=None) as batch_op:
        batch_op.drop_constraint('ck_cell_not_included_cited', type_='check')
        batch_op.create_check_constraint(
            'ck_cell_not_included_cited', "state <> 'not_included' OR clause_id IS NOT NULL"
        )
        batch_op.drop_constraint('fk_comparison_cells_paragraph_id', type_='foreignkey')
        batch_op.drop_column('paragraphs_hash')
        batch_op.drop_column('paragraph_id')
