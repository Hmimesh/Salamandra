"""Add cancelled events and organization-owned kits.

Revision ID: 0006_staging_usability
Revises: 0005_workspace_registration
Create Date: 2026-09-04
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0006_staging_usability"
down_revision = "0005_workspace_registration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("events") as batch:
        batch.drop_constraint("ck_events_status", type_="check")
        batch.create_check_constraint(
            "ck_events_status",
            "status IN ('planning', 'confirmed', 'packed', 'out', 'returned', 'cancelled')",
        )
    op.create_table(
        "saved_kits",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "data",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_saved_kits_version"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id", "organization_id", name="uq_saved_kits_id_org"
        ),
    )
    op.create_index(
        "ix_saved_kits_org_name", "saved_kits", ["organization_id", "name"]
    )


def downgrade() -> None:
    op.drop_index("ix_saved_kits_org_name", table_name="saved_kits")
    op.drop_table("saved_kits")
    with op.batch_alter_table("events") as batch:
        batch.drop_constraint("ck_events_status", type_="check")
        batch.create_check_constraint(
            "ck_events_status",
            "status IN ('planning', 'confirmed', 'packed', 'out', 'returned')",
        )
