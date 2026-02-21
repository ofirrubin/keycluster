"""add realm_themes table

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-02-20 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'realm_themes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('realm', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column('theme_name', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False, server_default=sa.text("'dynamic-standard'")),
        sa.Column('config', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_realm_themes_realm'), 'realm_themes', ['realm'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_realm_themes_realm'), table_name='realm_themes')
    op.drop_table('realm_themes')
