"""Add durable inventory reconciliation ledger and holding archival.

Revision ID: 0004_inventory_ledger
Revises: 0003_operation_requests
Create Date: 2026-08-29
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004_inventory_ledger"
down_revision = "0003_operation_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("inventory_holdings") as batch:
        batch.add_column(
            sa.Column(
                "active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )

    op.create_table(
        "inventory_adjustments",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("holding_id", sa.String(length=64), nullable=False),
        sa.Column("item_id", sa.String(length=256), nullable=False),
        sa.Column("actor_membership_id", sa.String(length=64), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("before_quantity", sa.Integer(), nullable=False),
        sa.Column("after_quantity", sa.Integer(), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=300), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("request_id", sa.String(length=160), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("batch_id", sa.String(length=160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("before_quantity >= 0", name="ck_adjustments_before"),
        sa.CheckConstraint("after_quantity >= 0", name="ck_adjustments_after"),
        sa.CheckConstraint(
            "after_quantity - before_quantity = delta",
            name="ck_adjustments_delta",
        ),
        sa.ForeignKeyConstraint(
            ["actor_membership_id", "organization_id"],
            ["memberships.id", "memberships.organization_id"],
            name="fk_adjustments_actor_same_org",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["holding_id", "organization_id"],
            ["inventory_holdings.id", "inventory_holdings.organization_id"],
            name="fk_adjustments_holding_same_org",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "operation",
            "idempotency_key",
            "holding_id",
            name="uq_adjustments_operation_holding_key",
        ),
    )
    op.create_index(
        "ix_adjustments_org_holding_created",
        "inventory_adjustments",
        ["organization_id", "holding_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_adjustments_org_batch",
        "inventory_adjustments",
        ["organization_id", "batch_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_adjustments_org_batch", table_name="inventory_adjustments"
    )
    op.drop_index(
        "ix_adjustments_org_holding_created",
        table_name="inventory_adjustments",
    )
    op.drop_table("inventory_adjustments")
    with op.batch_alter_table("inventory_holdings") as batch:
        batch.drop_column("active")
