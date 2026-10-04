"""evidence 근거 청크와 상황 테이블

Revision ID: db09f1c76a36
Revises: f34cfabf0a8e
Create Date: 2026-10-04 18:58:05.951913

"""
from typing import Sequence, Union

from alembic import op
import pgvector.sqlalchemy
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'db09f1c76a36'
down_revision: Union[str, Sequence[str], None] = 'f34cfabf0a8e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """스키마 올리기"""
    op.create_table('evidence_chunks',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('paper', sa.Text(), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('population', sa.Text(), nullable=False),
    sa.Column('method', sa.Text(), nullable=False),
    sa.Column('effect', sa.Text(), nullable=False),
    sa.Column('propositions', postgresql.ARRAY(sa.Text()), nullable=False),
    sa.Column('source_text', sa.Text(), nullable=False),
    sa.Column('citation', sa.Text(), nullable=False),
    sa.Column('doi_url', sa.Text(), nullable=False),
    sa.Column('license', sa.Text(), nullable=False),
    sa.Column('population_embedding', pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=False),
    sa.Column('embedding_model', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('evidence_situations',
    sa.Column('id', sa.Integer(), sa.Identity(always=True), nullable=False),
    sa.Column('chunk_id', sa.Text(), nullable=False),
    sa.Column('situation', sa.Text(), nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['evidence_chunks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_evidence_situations_chunk_id'), 'evidence_situations', ['chunk_id'], unique=False)


def downgrade() -> None:
    """스키마 내리기"""
    op.drop_index(op.f('ix_evidence_situations_chunk_id'), table_name='evidence_situations')
    op.drop_table('evidence_situations')
    op.drop_table('evidence_chunks')
