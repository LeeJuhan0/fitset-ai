"""routines 검색 테이블

Revision ID: f34cfabf0a8e
Revises: 
Create Date: 2026-09-24 19:09:40.618789

"""
from typing import Sequence, Union

from alembic import op
import pgvector.sqlalchemy
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'f34cfabf0a8e'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """스키마 올리기"""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table('routines',
    sa.Column('id', sa.Integer(), sa.Identity(always=True), nullable=False),
    sa.Column('slug', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('goal', sa.Text(), nullable=True),
    sa.Column('level', sa.SmallInteger(), nullable=False),
    sa.Column('minutes', sa.SmallInteger(), nullable=True),
    sa.Column('muscle_groups', postgresql.ARRAY(sa.Text()), nullable=False),
    sa.Column('equipment', postgresql.ARRAY(sa.Text()), nullable=False),
    sa.Column('bodyweight_only', sa.Boolean(), nullable=False),
    sa.Column('exercise_count', sa.SmallInteger(), nullable=False),
    sa.Column('exercise_names', postgresql.ARRAY(sa.Text()), nullable=False),
    sa.Column('body', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=False),
    sa.Column('embedding_model', sa.Text(), nullable=False),
    sa.Column('source', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('slug')
    )
    op.create_index('routines_bodyweight', 'routines', ['level', 'minutes'], unique=False, postgresql_where='bodyweight_only')
    op.create_index('routines_level_minutes', 'routines', ['level', 'minutes'], unique=False)
    op.create_index('routines_muscle_gin', 'routines', ['muscle_groups'], unique=False, postgresql_using='gin')


def downgrade() -> None:
    """스키마 내리기"""
    op.drop_index('routines_muscle_gin', table_name='routines', postgresql_using='gin')
    op.drop_index('routines_level_minutes', table_name='routines')
    op.drop_index('routines_bodyweight', table_name='routines', postgresql_where='bodyweight_only')
    op.drop_table('routines')
