"""Add durable operation idempotency records.

Revision ID: 0003_operation_requests
Revises: 0002_runtime_core
Create Date: 2026-08-29
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0003_operation_requests"
down_revision = "0002_runtime_core"
branch_labels = None
depends_on = None


JSON_VALUE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "operation_requests",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=True),
        sa.Column("response", JSON_VALUE, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('processing', 'completed')",
            name="ck_operation_requests_status",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "operation",
            "idempotency_key",
            name="uq_operation_requests_org_operation_key",
        ),
    )
    op.create_index(
        "ix_operation_requests_org_resource",
        "operation_requests",
        ["organization_id", "resource_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_operation_requests_org_resource", table_name="operation_requests"
    )
    op.drop_table("operation_requests")
