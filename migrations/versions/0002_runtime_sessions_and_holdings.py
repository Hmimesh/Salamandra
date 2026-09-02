"""Add durable session context and unambiguous holding identities.

Revision ID: 0002_runtime_core
Revises: 0001_transactional_core
Create Date: 2026-08-28
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_runtime_core"
down_revision = "0001_transactional_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Sessions are intentionally invalidated at this security boundary.
    op.execute("DELETE FROM sessions")
    with op.batch_alter_table("sessions") as batch:
        batch.add_column(
            sa.Column("organization_id", sa.String(length=64), nullable=False)
        )
        batch.add_column(
            sa.Column("membership_id", sa.String(length=64), nullable=False)
        )
        batch.create_foreign_key(
            "fk_sessions_membership_same_org",
            "memberships",
            ["membership_id", "organization_id"],
            ["id", "organization_id"],
            ondelete="CASCADE",
        )
        batch.create_index(
            "ix_sessions_user_active",
            ["user_id", "revoked_at", "expires_at"],
            unique=False,
        )
    op.create_index(
        "uq_holdings_shared_item",
        "inventory_holdings",
        ["organization_id", "legacy_item_id"],
        unique=True,
        postgresql_where=sa.text("scope = 'shared'"),
    )
    op.create_index(
        "uq_holdings_personal_item",
        "inventory_holdings",
        ["organization_id", "owner_user_id", "legacy_item_id"],
        unique=True,
        postgresql_where=sa.text("scope = 'personal'"),
    )


def downgrade() -> None:
    op.drop_index("uq_holdings_personal_item", table_name="inventory_holdings")
    op.drop_index("uq_holdings_shared_item", table_name="inventory_holdings")
    with op.batch_alter_table("sessions") as batch:
        batch.drop_index("ix_sessions_user_active")
        batch.drop_constraint("fk_sessions_membership_same_org", type_="foreignkey")
        batch.drop_column("membership_id")
        batch.drop_column("organization_id")
