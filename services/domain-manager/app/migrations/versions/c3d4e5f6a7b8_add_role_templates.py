"""add role_templates table with default seeds

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-02-20 14:00:00.000000

"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Default role template seeds
_ECOMMERCE_ROLES = json.dumps([
    {"name": "admin", "description": "Full store administration access"},
    {"name": "moderator", "description": "Content and order moderation"},
    {"name": "customer", "description": "Registered customer with purchase access"},
    {"name": "b2b_user", "description": "Business-to-business buyer with bulk pricing"},
    {"name": "selling_agent", "description": "Sales agent with commission tracking"},
    {"name": "viewer", "description": "Read-only catalog browsing"},
])

_SAAS_ROLES = json.dumps([
    {"name": "admin", "description": "Full application administration"},
    {"name": "user", "description": "Standard user access"},
    {"name": "billing_admin", "description": "Billing and subscription management"},
    {"name": "support", "description": "Customer support and ticket access"},
])

_MINIMAL_ROLES = json.dumps([
    {"name": "admin", "description": "Full administration access"},
    {"name": "user", "description": "Standard user access"},
])


def upgrade() -> None:
    op.create_table(
        "role_templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "name",
            sqlmodel.sql.sqltypes.AutoString(length=100),
            nullable=False,
        ),
        sa.Column(
            "description",
            sqlmodel.sql.sqltypes.AutoString(length=500),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column(
            "roles",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_role_templates_name"), "role_templates", ["name"], unique=True)

    # Seed default templates via raw SQL for correct timestamp handling
    op.execute(
        sa.text(
            "INSERT INTO role_templates (name, description, roles, is_default, created_at, updated_at) VALUES "
            "(:n1, :d1, :r1::jsonb, true, NOW(), NOW()), "
            "(:n2, :d2, :r2::jsonb, true, NOW(), NOW()), "
            "(:n3, :d3, :r3::jsonb, true, NOW(), NOW())"
        ).bindparams(
            n1="ecommerce", d1="Standard eCommerce roles for online stores", r1=_ECOMMERCE_ROLES,
            n2="saas", d2="Standard SaaS application roles", r2=_SAAS_ROLES,
            n3="minimal", d3="Minimal role set for simple applications", r3=_MINIMAL_ROLES,
        )
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_role_templates_name"), table_name="role_templates")
    op.drop_table("role_templates")
