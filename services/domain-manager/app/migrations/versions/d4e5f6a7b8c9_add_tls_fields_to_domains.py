"""add tls fields to domains

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-02-21 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "domainmapping",
        sa.Column(
            "tls_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "domainmapping",
        sa.Column("tls_secret_name", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("domainmapping", "tls_secret_name")
    op.drop_column("domainmapping", "tls_enabled")
