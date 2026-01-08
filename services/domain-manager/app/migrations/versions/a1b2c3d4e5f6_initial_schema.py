"""initial_schema

Revision ID: a1b2c3d4e5f6
Revises: 
Create Date: 2024-01-08 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'domainmapping',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('realm', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('domain', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('theme_name', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=sa.text("'dynamic-standard'")),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_domainmapping_realm'), 'domainmapping', ['realm'], unique=True)
    op.create_index(op.f('ix_domainmapping_domain'), 'domainmapping', ['domain'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_domainmapping_domain'), table_name='domainmapping')
    op.drop_index(op.f('ix_domainmapping_realm'), table_name='domainmapping')
    op.drop_table('domainmapping')
