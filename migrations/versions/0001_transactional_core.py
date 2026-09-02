"""Create Salamandra transactional core schema.

Revision ID: 0001_transactional_core
Revises:
Create Date: 2026-08-21
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_transactional_core"
down_revision = None
branch_labels = None
depends_on = None


JSON_VALUE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("preferences", JSON_VALUE, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'disabled')",
            name="ck_users_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_table(
        "integration_connections",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("integration_id", sa.String(length=100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("data", JSON_VALUE, nullable=False),
        sa.CheckConstraint(
            "version > 0",
            name="ck_integration_connections_version",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "integration_id",
            name="uq_integration_connections_org_integration",
        ),
    )
    op.create_table(
        "item_class_records",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("class_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("data", JSON_VALUE, nullable=False),
        sa.CheckConstraint("version > 0", name="ck_item_class_records_version"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "class_id",
            name="uq_item_class_records_org_class",
        ),
    )
    op.create_table(
        "memberships",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_memberships_version"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_memberships_id_org"),
        sa.UniqueConstraint(
            "organization_id",
            "user_id",
            name="uq_membership_org_user",
        ),
    )
    op.create_index(
        "ix_memberships_org_role",
        "memberships",
        ["organization_id", "role"],
        unique=False,
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("actor_membership_id", sa.String(length=64), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("resource_type", sa.String(length=100), nullable=False),
        sa.Column("resource_id", sa.String(length=128), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("changes", JSON_VALUE, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_membership_id", "organization_id"],
            ["memberships.id", "memberships.organization_id"],
            name="fk_audit_actor_same_org",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_org_created",
        "audit_events",
        ["organization_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "events",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("priority_score", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("data", JSON_VALUE, nullable=False),
        sa.CheckConstraint(
            "status IN ('planning', 'confirmed', 'packed', 'out', 'returned')",
            name="ck_events_status",
        ),
        sa.CheckConstraint("version > 0", name="ck_events_version"),
        sa.ForeignKeyConstraint(
            ["organization_id", "owner_user_id"],
            ["memberships.organization_id", "memberships.user_id"],
            name="fk_events_owner_same_org",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_events_id_org"),
    )
    op.create_index(
        "ix_events_org_window",
        "events",
        ["organization_id", "status", "starts_at", "ends_at"],
        unique=False,
    )
    op.create_table(
        "inventory_holdings",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("legacy_item_id", sa.String(length=256), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=True),
        sa.Column("available_quantity", sa.Integer(), nullable=False),
        sa.Column("reserved_quantity", sa.Integer(), nullable=False),
        sa.Column("packed_quantity", sa.Integer(), nullable=False),
        sa.Column("dispatched_quantity", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("data", JSON_VALUE, nullable=False),
        sa.CheckConstraint(
            "(scope = 'shared' AND owner_user_id IS NULL) OR "
            "(scope = 'personal' AND owner_user_id IS NOT NULL)",
            name="ck_holdings_scope_owner",
        ),
        sa.CheckConstraint(
            "scope IN ('shared', 'personal')",
            name="ck_holdings_scope",
        ),
        sa.CheckConstraint(
            "available_quantity >= 0",
            name="ck_holdings_available",
        ),
        sa.CheckConstraint(
            "reserved_quantity >= 0",
            name="ck_holdings_reserved",
        ),
        sa.CheckConstraint("packed_quantity >= 0", name="ck_holdings_packed"),
        sa.CheckConstraint(
            "dispatched_quantity >= 0",
            name="ck_holdings_dispatched",
        ),
        sa.CheckConstraint("version > 0", name="ck_holdings_version"),
        sa.ForeignKeyConstraint(
            ["organization_id", "owner_user_id"],
            ["memberships.organization_id", "memberships.user_id"],
            name="fk_holdings_owner_same_org",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_holdings_id_org"),
        sa.UniqueConstraint(
            "organization_id",
            "scope",
            "owner_user_id",
            "legacy_item_id",
            name="uq_holdings_identity",
        ),
    )
    op.create_index(
        "ix_holdings_org_item",
        "inventory_holdings",
        ["organization_id", "legacy_item_id"],
        unique=False,
    )
    op.create_table(
        "allocations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "status IN ('reserved', 'packed', 'dispatched', 'returned')",
            name="ck_allocations_status",
        ),
        sa.CheckConstraint("version > 0", name="ck_allocations_version"),
        sa.ForeignKeyConstraint(
            ["event_id", "organization_id"],
            ["events.id", "events.organization_id"],
            name="fk_allocations_event_same_org",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_allocations_id_org"),
    )
    op.create_index(
        "ix_allocations_org_status",
        "allocations",
        ["organization_id", "status"],
        unique=False,
    )
    op.create_table(
        "stock_movements",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("actor_membership_id", sa.String(length=64), nullable=False),
        sa.Column("lines", JSON_VALUE, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_membership_id", "organization_id"],
            ["memberships.id", "memberships.organization_id"],
            name="fk_movements_actor_same_org",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["event_id", "organization_id"],
            ["events.id", "events.organization_id"],
            name="fk_movements_event_same_org",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_movements_idempotency",
        ),
    )
    op.create_index(
        "ix_movements_org_event",
        "stock_movements",
        ["organization_id", "event_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "allocation_lines",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("allocation_id", sa.String(length=64), nullable=False),
        sa.Column("holding_id", sa.String(length=64), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "state IN ('reserved', 'packed', 'dispatched', 'returned')",
            name="ck_allocation_lines_state",
        ),
        sa.CheckConstraint("quantity > 0", name="ck_allocation_lines_quantity"),
        sa.ForeignKeyConstraint(
            ["allocation_id", "organization_id"],
            ["allocations.id", "allocations.organization_id"],
            name="fk_allocation_lines_allocation_same_org",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["holding_id", "organization_id"],
            ["inventory_holdings.id", "inventory_holdings.organization_id"],
            name="fk_allocation_lines_holding_same_org",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_allocation_lines_allocation",
        "allocation_lines",
        ["organization_id", "allocation_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_allocation_lines_allocation", table_name="allocation_lines")
    op.drop_table("allocation_lines")
    op.drop_index("ix_movements_org_event", table_name="stock_movements")
    op.drop_table("stock_movements")
    op.drop_index("ix_allocations_org_status", table_name="allocations")
    op.drop_table("allocations")
    op.drop_index("ix_holdings_org_item", table_name="inventory_holdings")
    op.drop_table("inventory_holdings")
    op.drop_index("ix_events_org_window", table_name="events")
    op.drop_table("events")
    op.drop_index("ix_audit_org_created", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("sessions")
    op.drop_index("ix_memberships_org_role", table_name="memberships")
    op.drop_table("memberships")
    op.drop_table("item_class_records")
    op.drop_table("integration_connections")
    op.drop_table("users")
    op.drop_table("organizations")
