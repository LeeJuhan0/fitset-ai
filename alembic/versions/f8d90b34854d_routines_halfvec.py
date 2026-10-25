"""routines 임베딩 halfvec 전환

Revision ID: f8d90b34854d
Revises: db09f1c76a36
Create Date: 2026-10-25 13:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import pgvector.sqlalchemy

revision: str = 'f8d90b34854d'
down_revision: Union[str, Sequence[str], None] = 'db09f1c76a36'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """스키마 올리기"""
    op.alter_column('routines', 'embedding', type_=pgvector.sqlalchemy.halfvec.HALFVEC(dim=1024),
                    postgresql_using='embedding::halfvec(1024)')


def downgrade() -> None:
    """스키마 내리기"""
    op.alter_column('routines', 'embedding', type_=pgvector.sqlalchemy.vector.VECTOR(dim=1024),
                    postgresql_using='embedding::vector(1024)')
