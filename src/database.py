from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    and_,
    create_engine,
    delete,
    or_,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from event_operations import EVENT_TRANSITIONS
from Item_node import Requirement
from inventory_dependencies import (
    DependencyTargetInUse,
    DependencyNode,
    NodeKey,
    normalize_dependency_requirements,
    validate_dependency_updates,
)
from security import Permission, ResourceNotFound, StateConflict, require_permission


JSON_VALUE = JSON().with_variant(JSONB, "postgresql")
EVENT_STATUSES = tuple(EVENT_TRANSITIONS)


def new_id() -> str:
    return uuid4().hex


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class OrganizationModel(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CatalogTermModel(Base):
    __tablename__ = "catalog_terms"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(String(64), ForeignKey("organizations.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))
    label: Mapped[str] = mapped_column(String(200))
    normalized_label: Mapped[str] = mapped_column(String(256))
    canonical_code: Mapped[str] = mapped_column(String(80))
    language: Mapped[str] = mapped_column(String(32), default="")
    confirmed_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("organization_id", "normalized_label", name="uq_catalog_terms_org_label"),
        CheckConstraint("kind IN ('category', 'alias')", name="ck_catalog_terms_kind"),
        ForeignKeyConstraint(["confirmed_by", "organization_id"],
                             ["memberships.id", "memberships.organization_id"],
                             ondelete="RESTRICT", name="fk_catalog_terms_actor_org"),
    )


class CatalogDecisionModel(Base):
    __tablename__ = "catalog_decisions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(String(64), ForeignKey("organizations.id", ondelete="CASCADE"))
    pair_key: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(16))
    data: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict)
    __table_args__ = (
        UniqueConstraint("organization_id", "pair_key", name="uq_catalog_decisions_org_pair"),
        CheckConstraint("action IN ('separate', 'same')", name="ck_catalog_decisions_action"),
    )


class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    preferences: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        CheckConstraint("status IN ('active', 'disabled')", name="ck_users_status"),
        Index("uq_users_email_normalized", text("lower(email)"), unique=True),
    )


class MembershipModel(Base):
    __tablename__ = "memberships"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_memberships_id_org"),
        UniqueConstraint("organization_id", "user_id", name="uq_membership_org_user"),
        CheckConstraint("version > 0", name="ck_memberships_version"),
        Index("ix_memberships_org_role", "organization_id", "role"),
    )


class InventoryHoldingModel(Base):
    __tablename__ = "inventory_holdings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    legacy_item_id: Mapped[str] = mapped_column(String(256), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    available_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reserved_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    packed_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dispatched_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    data: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict)

    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_holdings_id_org"),
        UniqueConstraint(
            "organization_id",
            "scope",
            "owner_user_id",
            "legacy_item_id",
            name="uq_holdings_identity",
        ),
        CheckConstraint("scope IN ('shared', 'personal')", name="ck_holdings_scope"),
        CheckConstraint(
            "(scope = 'shared' AND owner_user_id IS NULL) OR "
            "(scope = 'personal' AND owner_user_id IS NOT NULL)",
            name="ck_holdings_scope_owner",
        ),
        CheckConstraint("available_quantity >= 0", name="ck_holdings_available"),
        CheckConstraint("reserved_quantity >= 0", name="ck_holdings_reserved"),
        CheckConstraint("packed_quantity >= 0", name="ck_holdings_packed"),
        CheckConstraint("dispatched_quantity >= 0", name="ck_holdings_dispatched"),
        CheckConstraint("version > 0", name="ck_holdings_version"),
        ForeignKeyConstraint(
            ["organization_id", "owner_user_id"],
            ["memberships.organization_id", "memberships.user_id"],
            ondelete="RESTRICT",
            name="fk_holdings_owner_same_org",
        ),
        Index("ix_holdings_org_item", "organization_id", "legacy_item_id"),
        Index(
            "uq_holdings_shared_item",
            "organization_id",
            "legacy_item_id",
            unique=True,
            postgresql_where=text("scope = 'shared'"),
            sqlite_where=text("scope = 'shared'"),
        ),
        Index(
            "uq_holdings_personal_item",
            "organization_id",
            "owner_user_id",
            "legacy_item_id",
            unique=True,
            postgresql_where=text("scope = 'personal'"),
            sqlite_where=text("scope = 'personal'"),
        ),
    )


class EventModel(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    owner_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="planning")
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    priority_score: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    data: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict)

    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_events_id_org"),
        CheckConstraint(
            "status IN ('planning', 'confirmed', 'packed', 'out', 'returned', 'cancelled')",
            name="ck_events_status",
        ),
        CheckConstraint("version > 0", name="ck_events_version"),
        ForeignKeyConstraint(
            ["organization_id", "owner_user_id"],
            ["memberships.organization_id", "memberships.user_id"],
            ondelete="RESTRICT",
            name="fk_events_owner_same_org",
        ),
        Index("ix_events_org_window", "organization_id", "status", "starts_at", "ends_at"),
    )


class EventLearningRecordModel(Base):
    __tablename__ = "event_learning_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="real")
    source_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    exclusion_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    features: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict)
    original_request: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict)
    proposal: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict)
    corrections: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict)
    execution: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_learning_records_id_org"),
        ForeignKeyConstraint(
            ["event_id", "organization_id"],
            ["events.id", "events.organization_id"],
            ondelete="CASCADE",
            name="fk_learning_records_event_same_org",
        ),
        CheckConstraint("version > 0", name="ck_learning_records_version"),
        Index("ix_learning_records_org_eligible", "organization_id", "eligible", "event_id"),
    )


class EventFeedbackModel(Base):
    __tablename__ = "event_feedback"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    missing: Mapped[str] = mapped_column(String(8), nullable=False)
    unnecessary: Mapped[str] = mapped_column(String(8), nullable=False)
    failed: Mapped[str] = mapped_column(String(8), nullable=False)
    additional_onsite: Mapped[str] = mapped_column(String(8), nullable=False)
    plan_fit: Mapped[str] = mapped_column(String(24), nullable=False)
    reuse_plan: Mapped[str] = mapped_column(String(24), nullable=False)
    notes: Mapped[str] = mapped_column(String(2000), nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_event_feedback_id_org"),
        ForeignKeyConstraint(
            ["event_id", "organization_id"],
            ["events.id", "events.organization_id"],
            ondelete="CASCADE",
            name="fk_event_feedback_event_same_org",
        ),
        CheckConstraint("missing IN ('yes', 'no')", name="ck_event_feedback_missing"),
        CheckConstraint("unnecessary IN ('yes', 'no')", name="ck_event_feedback_unnecessary"),
        CheckConstraint("failed IN ('yes', 'no')", name="ck_event_feedback_failed"),
        CheckConstraint("additional_onsite IN ('yes', 'no')", name="ck_event_feedback_additional"),
        CheckConstraint("plan_fit IN ('too_little', 'about_right', 'too_much')", name="ck_event_feedback_plan_fit"),
        CheckConstraint("reuse_plan IN ('yes', 'with_changes', 'no')", name="ck_event_feedback_reuse"),
        CheckConstraint("version > 0", name="ck_event_feedback_version"),
        Index("ix_event_feedback_org_event", "organization_id", "event_id"),
    )


class EventFeedbackItemModel(Base):
    __tablename__ = "event_feedback_items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(String(64), nullable=False)
    feedback_id: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    item_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    label_snapshot: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str] = mapped_column(String(500), nullable=False, default="")

    __table_args__ = (
        ForeignKeyConstraint(
            ["feedback_id", "organization_id"],
            ["event_feedback.id", "event_feedback.organization_id"],
            ondelete="CASCADE",
            name="fk_event_feedback_items_feedback_same_org",
        ),
        CheckConstraint("kind IN ('missing', 'unnecessary', 'failed', 'additional_onsite')", name="ck_event_feedback_items_kind"),
        CheckConstraint("quantity IS NULL OR quantity > 0", name="ck_event_feedback_items_quantity"),
        Index("ix_event_feedback_items_org_feedback", "organization_id", "feedback_id"),
    )


class SavedKitModel(Base):
    __tablename__ = "saved_kits"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    data: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_saved_kits_id_org"),
        CheckConstraint("version > 0", name="ck_saved_kits_version"),
        Index("ix_saved_kits_org_name", "organization_id", "name"),
    )


class ItemClassRecordModel(Base):
    __tablename__ = "item_class_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    class_id: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    data: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict)

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "class_id", name="uq_item_class_records_org_class"
        ),
        CheckConstraint("version > 0", name="ck_item_class_records_version"),
    )


class IntegrationConnectionModel(Base):
    __tablename__ = "integration_connections"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    integration_id: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    data: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict)

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "integration_id",
            name="uq_integration_connections_org_integration",
        ),
        CheckConstraint("version > 0", name="ck_integration_connections_version"),
    )


class AllocationModel(Base):
    __tablename__ = "allocations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="reserved")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_allocations_id_org"),
        ForeignKeyConstraint(
            ["event_id", "organization_id"],
            ["events.id", "events.organization_id"],
            ondelete="CASCADE",
            name="fk_allocations_event_same_org",
        ),
        CheckConstraint(
            "status IN ('reserved', 'packed', 'dispatched', 'returned')",
            name="ck_allocations_status",
        ),
        CheckConstraint("version > 0", name="ck_allocations_version"),
        Index("ix_allocations_org_status", "organization_id", "status"),
    )


class AllocationLineModel(Base):
    __tablename__ = "allocation_lines"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(String(64), nullable=False)
    allocation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    holding_id: Mapped[str] = mapped_column(String(64), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="reserved")

    __table_args__ = (
        ForeignKeyConstraint(
            ["allocation_id", "organization_id"],
            ["allocations.id", "allocations.organization_id"],
            ondelete="CASCADE",
            name="fk_allocation_lines_allocation_same_org",
        ),
        ForeignKeyConstraint(
            ["holding_id", "organization_id"],
            ["inventory_holdings.id", "inventory_holdings.organization_id"],
            ondelete="RESTRICT",
            name="fk_allocation_lines_holding_same_org",
        ),
        CheckConstraint("quantity > 0", name="ck_allocation_lines_quantity"),
        CheckConstraint(
            "state IN ('reserved', 'packed', 'dispatched', 'returned')",
            name="ck_allocation_lines_state",
        ),
        Index("ix_allocation_lines_allocation", "organization_id", "allocation_id"),
    )


class StockMovementModel(Base):
    __tablename__ = "stock_movements"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    actor_membership_id: Mapped[str] = mapped_column(String(64), nullable=False)
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSON_VALUE, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["event_id", "organization_id"],
            ["events.id", "events.organization_id"],
            ondelete="RESTRICT",
            name="fk_movements_event_same_org",
        ),
        ForeignKeyConstraint(
            ["actor_membership_id", "organization_id"],
            ["memberships.id", "memberships.organization_id"],
            ondelete="RESTRICT",
            name="fk_movements_actor_same_org",
        ),
        UniqueConstraint(
            "organization_id", "idempotency_key", name="uq_movements_idempotency"
        ),
        Index("ix_movements_org_event", "organization_id", "event_id", "created_at"),
    )


class InventoryAdjustmentModel(Base):
    __tablename__ = "inventory_adjustments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(String(64), nullable=False)
    holding_id: Mapped[str] = mapped_column(String(64), nullable=False)
    item_id: Mapped[str] = mapped_column(String(256), nullable=False)
    actor_membership_id: Mapped[str] = mapped_column(String(64), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    before_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    after_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    request_id: Mapped[str] = mapped_column(String(160), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    batch_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["holding_id", "organization_id"],
            ["inventory_holdings.id", "inventory_holdings.organization_id"],
            ondelete="RESTRICT",
            name="fk_adjustments_holding_same_org",
        ),
        ForeignKeyConstraint(
            ["actor_membership_id", "organization_id"],
            ["memberships.id", "memberships.organization_id"],
            ondelete="RESTRICT",
            name="fk_adjustments_actor_same_org",
        ),
        CheckConstraint("before_quantity >= 0", name="ck_adjustments_before"),
        CheckConstraint("after_quantity >= 0", name="ck_adjustments_after"),
        CheckConstraint(
            "after_quantity - before_quantity = delta",
            name="ck_adjustments_delta",
        ),
        UniqueConstraint(
            "organization_id",
            "operation",
            "idempotency_key",
            "holding_id",
            name="uq_adjustments_operation_holding_key",
        ),
        Index(
            "ix_adjustments_org_holding_created",
            "organization_id",
            "holding_id",
            "created_at",
        ),
        Index(
            "ix_adjustments_org_batch",
            "organization_id",
            "batch_id",
        ),
    )


class AuditEventModel(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    actor_membership_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    changes: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["actor_membership_id", "organization_id"],
            ["memberships.id", "memberships.organization_id"],
            ondelete="RESTRICT",
            name="fk_audit_actor_same_org",
        ),
        Index("ix_audit_org_created", "organization_id", "created_at"),
    )


class SessionModel(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[str] = mapped_column(String(64), nullable=False)
    membership_id: Mapped[str] = mapped_column(String(64), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["membership_id", "organization_id"],
            ["memberships.id", "memberships.organization_id"],
            ondelete="CASCADE",
            name="fk_sessions_membership_same_org",
        ),
        Index("ix_sessions_user_active", "user_id", "revoked_at", "expires_at"),
    )


class OperationRequestModel(Base):
    __tablename__ = "operation_requests"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="processing")
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    response: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "operation",
            "idempotency_key",
            name="uq_operation_requests_org_operation_key",
        ),
        CheckConstraint(
            "status IN ('processing', 'completed')",
            name="ck_operation_requests_status",
        ),
        Index(
            "ix_operation_requests_org_resource",
            "organization_id",
            "resource_id",
        ),
    )


def create_database_engine(database_url: str, *, production: bool = False) -> Engine:
    if production and not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise ValueError("Production persistence requires PostgreSQL.")
    return create_engine(database_url, future=True, pool_pre_ping=True)


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def active_membership(
    session: Session,
    organization_id: str,
    user_id: str,
) -> MembershipModel:
    membership = session.scalar(
        select(MembershipModel).where(
            MembershipModel.organization_id == organization_id,
            MembershipModel.user_id == user_id,
            MembershipModel.status == "active",
        )
    )
    if membership is None:
        raise ResourceNotFound("Organization membership was not found.")
    return membership


def active_assignee_ids(
    session: Session,
    organization_id: str,
    owner_user_id: str,
    requested_ids: list[str] | tuple[str, ...],
) -> list[str]:
    ordered = list(dict.fromkeys([owner_user_id, *(str(value).strip() for value in requested_ids)]))
    if len(ordered) > 100 or any(not value for value in ordered):
        raise ValueError("Event crew selection is invalid.")
    active_ids = set(
        session.scalars(
            select(MembershipModel.user_id)
            .join(UserModel, UserModel.id == MembershipModel.user_id)
            .where(
                MembershipModel.organization_id == organization_id,
                MembershipModel.user_id.in_(ordered),
                MembershipModel.status == "active",
                UserModel.status == "active",
            )
        )
    )
    if active_ids != set(ordered):
        raise ValueError("Choose only active members of this workspace for the event crew.")
    return ordered


class TransactionalEventCreation:
    """Idempotent server-owned event creation."""

    OPERATION = "event.create"

    def __init__(self, factory: sessionmaker[Session]):
        self.factory = factory

    def create(
        self,
        organization_id: str,
        owner_user_id: str,
        idempotency_key: str,
        request_id: str,
        request_payload: dict[str, Any],
        event_data: dict[str, Any],
    ) -> tuple[EventModel, bool]:
        idempotency_key = idempotency_key.strip()
        if not organization_id or not owner_user_id:
            raise ValueError("Event creation context is incomplete.")
        if not idempotency_key or len(idempotency_key) > 160:
            raise ValueError("Event creation idempotency key is invalid.")
        fingerprint = hashlib.sha256(
            json.dumps(
                request_payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        try:
            with self.factory.begin() as session:
                membership = active_membership(
                    session, organization_id, owner_user_id
                )
                require_permission(membership.role, Permission.EVENTS_CREATE)
                organization = session.scalar(
                    select(OrganizationModel)
                    .where(OrganizationModel.id == organization_id)
                    .with_for_update()
                )
                if organization is None:
                    raise ResourceNotFound("Organization was not found.")

                existing = self._request(
                    session, organization_id, idempotency_key, lock=True
                )
                if existing is not None:
                    return self._existing_result(session, existing, fingerprint)

                operation = OperationRequestModel(
                    organization_id=organization_id,
                    operation=self.OPERATION,
                    idempotency_key=idempotency_key,
                    request_fingerprint=fingerprint,
                )
                session.add(operation)
                session.flush()

                event_id = new_id()
                authoritative_data = dict(event_data)
                assigned_user_ids = active_assignee_ids(
                    session,
                    organization_id,
                    owner_user_id,
                    list(authoritative_data.get("assigned_user_ids", [])),
                )
                authoritative_data.update(
                    {
                        "id": event_id,
                        "organization_id": organization_id,
                        "owner_id": owner_user_id,
                        "assigned_user_ids": assigned_user_ids,
                        "status": "planning",
                        "movements": [],
                        "plan_verified": True,
                        "version": 1,
                    }
                )
                starts_at = self._event_start(authoritative_data)
                duration_minutes = int(
                    authoritative_data.get("duration_minutes", 240)
                )
                event = EventModel(
                    id=event_id,
                    organization_id=organization_id,
                    owner_user_id=owner_user_id,
                    title=str(authoritative_data.get("title", "Untitled event")),
                    status="planning",
                    starts_at=starts_at,
                    ends_at=(
                        starts_at + timedelta(minutes=duration_minutes)
                        if starts_at
                        else None
                    ),
                    priority_score=int(authoritative_data.get("priority_score", 50)),
                    version=1,
                    data=authoritative_data,
                )
                session.add(event)
                session.flush()
                from event_learning import EventLearningStore
                EventLearningStore.create_in_session(session, event, request_payload)

                operation.status = "completed"
                operation.resource_id = event.id
                operation.response = {"event_id": event.id}
                operation.completed_at = utc_now()
                session.add(
                    AuditEventModel(
                        organization_id=organization_id,
                        actor_membership_id=membership.id,
                        action="event.created",
                        resource_type="event",
                        resource_id=event.id,
                        request_id=request_id,
                        changes={
                            "idempotency_key": idempotency_key,
                            "version": event.version,
                        },
                    )
                )
                return event, True
        except IntegrityError:
            with self.factory() as session:
                existing = self._request(
                    session, organization_id, idempotency_key, lock=False
                )
                if existing is None:
                    raise
                return self._existing_result(session, existing, fingerprint)

    def _request(
        self,
        session: Session,
        organization_id: str,
        idempotency_key: str,
        *,
        lock: bool,
    ) -> OperationRequestModel | None:
        statement = select(OperationRequestModel).where(
            OperationRequestModel.organization_id == organization_id,
            OperationRequestModel.operation == self.OPERATION,
            OperationRequestModel.idempotency_key == idempotency_key,
        )
        if lock:
            statement = statement.with_for_update()
        return session.scalar(statement)

    @staticmethod
    def _existing_result(
        session: Session,
        request: OperationRequestModel,
        fingerprint: str,
    ) -> tuple[EventModel, bool]:
        if request.request_fingerprint != fingerprint:
            raise StateConflict(
                "This idempotency key was already used for another event."
            )
        if request.status != "completed" or not request.resource_id:
            raise StateConflict("This event creation is still being processed.")
        event = session.scalar(
            select(EventModel).where(
                EventModel.id == request.resource_id,
                EventModel.organization_id == request.organization_id,
            )
        )
        if event is None:
            raise StateConflict("The completed event is unavailable.")
        return event, False

    @staticmethod
    def _event_start(data: dict[str, Any]) -> datetime | None:
        try:
            return datetime.fromisoformat(
                f"{data.get('start_date', '')}T{data.get('start_time', '10:00')}:00"
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            return None


class TransactionalEventDetails:
    """Narrow commands for event fields that do not own operational state."""

    def __init__(self, factory: sessionmaker[Session]):
        self.factory = factory

    def update_checklist(
        self,
        organization_id: str,
        event_id: str,
        phase: str,
        item_id: str,
        done: bool,
        actor_user_id: str,
        request_id: str,
    ) -> EventModel:
        if phase not in {"pack", "return"}:
            raise ValueError("Checklist phase is not supported.")
        checklist_field = "return_checklist" if phase == "return" else "checklist"
        permission = (
            Permission.OPERATIONS_RETURN
            if phase == "return"
            else Permission.OPERATIONS_PACK
        )
        with self.factory.begin() as session:
            membership = active_membership(session, organization_id, actor_user_id)
            require_permission(membership.role, permission)
            event = self._event(session, organization_id, event_id)
            allowed_statuses = (
                {"out", "returned"}
                if phase == "return"
                else {"confirmed", "packed", "out", "returned"}
            )
            if event.status not in allowed_statuses:
                raise StateConflict(
                    f"The {phase} checklist is not available while the event is {event.status}."
                )

            data = dict(event.data or {})
            checklist = [dict(item) for item in data.get(checklist_field, [])]
            matched = False
            changed = False
            for item in checklist:
                if str(item.get("item_id", "")).strip().lower() == item_id:
                    matched = True
                    if bool(item.get("done")) != done:
                        item["done"] = done
                        changed = True
            if not matched:
                raise ResourceNotFound("Checklist item was not found.")
            if not changed:
                return event
            if phase == "return" and event.status == "returned" and not done:
                raise StateConflict("A completed return checklist cannot be reopened.")

            history = list(data.get("history", []))
            history.append(
                {
                    "action": "checklist",
                    "actor_id": actor_user_id,
                    "note": f"{phase} checklist updated.",
                    "timestamp": utc_now().isoformat(),
                }
            )
            data[checklist_field] = checklist
            data["history"] = history
            event.data = data
            event.version += 1
            session.add(
                AuditEventModel(
                    organization_id=organization_id,
                    actor_membership_id=membership.id,
                    action="event.checklist",
                    resource_type="event",
                    resource_id=event.id,
                    request_id=request_id,
                    changes={
                        "phase": phase,
                        "item_id": item_id,
                        "done": done,
                        "version": event.version,
                    },
                )
            )
            return event

    def update_planning_plan(
        self,
        organization_id: str,
        event_id: str,
        plan: dict[str, Any],
        checklist: list[dict[str, Any]],
        return_checklist: list[dict[str, Any]],
        actor_user_id: str,
        request_id: str,
        note: str,
    ) -> EventModel:
        with self.factory.begin() as session:
            membership = active_membership(session, organization_id, actor_user_id)
            require_permission(membership.role, Permission.EVENTS_UPDATE)
            event = self._event(session, organization_id, event_id)
            if event.status != "planning":
                raise StateConflict("Only planning events can be reallocated.")
            if session.scalar(
                select(AllocationModel.id).where(
                    AllocationModel.organization_id == organization_id,
                    AllocationModel.event_id == event.id,
                )
            ):
                raise StateConflict("An allocated event cannot be replanned in place.")

            data = dict(event.data or {})
            data["plan"] = dict(plan)
            data["checklist"] = [dict(item) for item in checklist]
            data["return_checklist"] = [dict(item) for item in return_checklist]
            history = list(data.get("history", []))
            history.append(
                {
                    "action": "reallocated",
                    "actor_id": actor_user_id,
                    "note": note,
                    "timestamp": utc_now().isoformat(),
                }
            )
            data["history"] = history
            event.data = data
            event.version += 1
            session.add(
                AuditEventModel(
                    organization_id=organization_id,
                    actor_membership_id=membership.id,
                    action="event.reallocated",
                    resource_type="event",
                    resource_id=event.id,
                    request_id=request_id,
                    changes={"version": event.version},
                )
            )
            return event

    def update_event(
        self,
        organization_id: str,
        event_id: str,
        expected_version: int,
        event_data: dict[str, Any],
        actor_user_id: str,
        request_id: str,
    ) -> EventModel:
        if expected_version < 1:
            raise ValueError("Event version is invalid.")
        with self.factory.begin() as session:
            membership = active_membership(session, organization_id, actor_user_id)
            require_permission(membership.role, Permission.EVENTS_UPDATE)
            event = self._event(session, organization_id, event_id)
            if event.status != "planning":
                raise StateConflict(
                    "Only planning events can be edited. Confirmed, packed, "
                    "dispatched, and returned events are locked to protect inventory history."
                )
            if event.version != expected_version:
                raise StateConflict(
                    "This event changed after you opened it. Reload and review the latest version."
                )
            if session.scalar(
                select(AllocationModel.id).where(
                    AllocationModel.organization_id == organization_id,
                    AllocationModel.event_id == event.id,
                )
            ):
                raise StateConflict("An allocated event cannot be edited in place.")

            previous_data = dict(event.data or {})
            previous = {
                "title": event.title,
                "start_date": previous_data.get("start_date", ""),
                "start_time": previous_data.get("start_time", ""),
                "location": previous_data.get("location", ""),
                "attendee_count": previous_data.get("attendee_count", 0),
                "description": previous_data.get("description", ""),
            }
            authoritative_data = dict(event_data)
            assigned_user_ids = active_assignee_ids(
                session,
                organization_id,
                event.owner_user_id,
                list(
                    authoritative_data.get(
                        "assigned_user_ids",
                        previous_data.get("assigned_user_ids", [event.owner_user_id]),
                    )
                ),
            )
            authoritative_data.update(
                {
                    "id": event.id,
                    "organization_id": organization_id,
                    "owner_id": event.owner_user_id,
                    "assigned_user_ids": assigned_user_ids,
                    "source_type": previous_data.get("source_type", ""),
                    "source_id": previous_data.get("source_id", ""),
                    "status": "planning",
                    "movements": [],
                    "created_at": previous_data.get("created_at", utc_now().isoformat()),
                    "plan_verified": True,
                }
            )
            history = list(previous_data.get("history", []))
            history.append(
                {
                    "action": "edited",
                    "actor_id": actor_user_id,
                    "note": "Event details and plan updated.",
                    "timestamp": utc_now().isoformat(),
                }
            )
            authoritative_data["history"] = history

            starts_at = TransactionalEventCreation._event_start(authoritative_data)
            duration_minutes = int(authoritative_data.get("duration_minutes", 240))
            event.title = str(authoritative_data.get("title", "Untitled event"))
            event.starts_at = starts_at
            event.ends_at = (
                starts_at + timedelta(minutes=duration_minutes) if starts_at else None
            )
            event.priority_score = int(authoritative_data.get("priority_score", 50))
            event.version += 1
            authoritative_data["version"] = event.version
            event.data = authoritative_data
            from event_learning import EventLearningStore
            EventLearningStore.capture_edit_in_session(session, event, previous_data)

            current = {
                "title": event.title,
                "start_date": authoritative_data.get("start_date", ""),
                "start_time": authoritative_data.get("start_time", ""),
                "location": authoritative_data.get("location", ""),
                "attendee_count": authoritative_data.get("attendee_count", 0),
                "description": authoritative_data.get("description", ""),
            }
            changed_fields = sorted(
                field for field in current if current[field] != previous[field]
            )
            session.add(
                AuditEventModel(
                    organization_id=organization_id,
                    actor_membership_id=membership.id,
                    action="event.edited",
                    resource_type="event",
                    resource_id=event.id,
                    request_id=request_id,
                    changes={
                        "changed_fields": changed_fields,
                        "version": event.version,
                    },
                )
            )
            return event

    @staticmethod
    def _event(session: Session, organization_id: str, event_id: str) -> EventModel:
        event = session.scalar(
            select(EventModel)
            .where(
                EventModel.organization_id == organization_id,
                EventModel.id == event_id,
            )
            .with_for_update()
        )
        if event is None:
            raise ResourceNotFound("Event was not found.")
        return event


class TransactionalEventOperations:
    """PostgreSQL transaction boundary for reserve and event-state movements."""

    def __init__(self, factory: sessionmaker[Session]):
        self.factory = factory

    def transition(
        self,
        organization_id: str,
        event_id: str,
        next_status: str,
        actor_membership_id: str,
        request_id: str,
        idempotency_key: str | None = None,
    ) -> EventModel:
        with self.factory.begin() as session:
            return self.transition_in_session(
                session,
                organization_id,
                event_id,
                next_status,
                actor_membership_id,
                request_id,
                idempotency_key,
            )

    def transition_in_session(
        self,
        session: Session,
        organization_id: str,
        event_id: str,
        next_status: str,
        actor_membership_id: str,
        request_id: str,
        idempotency_key: str | None = None,
    ) -> EventModel:
        event = session.scalar(
            select(EventModel)
            .where(
                EventModel.id == event_id,
                EventModel.organization_id == organization_id,
            )
            .with_for_update()
        )
        if event is None:
            raise ResourceNotFound("Event was not found.")
        if next_status == event.status and next_status in {"out", "returned"}:
            return event
        if next_status not in EVENT_TRANSITIONS.get(event.status, frozenset()):
            raise StateConflict(
                f"Event cannot move from {event.status} to {next_status}."
            )

        event_data = dict(event.data or {})
        if next_status == "packed" and any(
            not item.get("done") for item in event_data.get("checklist", [])
        ):
            raise StateConflict(
                "Complete the packing checklist before marking the event packed."
            )
        if next_status == "out" and event_data.get("conflicts"):
            raise StateConflict("Resolve inventory conflicts before dispatching the event.")
        if next_status == "returned" and any(
            not item.get("done") for item in event_data.get("return_checklist", [])
        ):
            raise StateConflict(
                "Complete the return checklist before closing the event."
            )

        if next_status == "confirmed":
            self._reserve_plan(session, event)
        elif next_status == "packed":
            self._move_allocation(session, event, "reserved", "packed")
        elif next_status == "out":
            self._move_allocation(session, event, "packed", "dispatched")
        elif next_status == "returned":
            self._move_allocation(session, event, "dispatched", "returned")

        event.status = next_status
        event.version += 1
        event_data["status"] = next_status
        history = list(event_data.get("history", []))
        history.append(
            {
                "action": next_status,
                "actor_id": actor_membership_id,
                "note": f"Event marked {next_status}.",
                "timestamp": utc_now().isoformat(),
            }
        )
        event_data["history"] = history
        event.data = event_data
        session.add(
            StockMovementModel(
                organization_id=organization_id,
                event_id=event.id,
                action=next_status,
                idempotency_key=idempotency_key or f"{event.id}:{next_status}",
                actor_membership_id=actor_membership_id,
                lines=self._movement_lines(session, event),
            )
        )
        from event_learning import EventLearningStore
        EventLearningStore.sync_execution_in_session(session, event)
        session.add(
            AuditEventModel(
                organization_id=organization_id,
                actor_membership_id=actor_membership_id,
                action=f"event.{next_status}",
                resource_type="event",
                resource_id=event.id,
                request_id=request_id,
                changes={"status": next_status, "version": event.version},
            )
        )
        return event

    def cancel(
        self,
        organization_id: str,
        event_id: str,
        actor_user_id: str,
        request_id: str,
    ) -> EventModel:
        with self.factory.begin() as session:
            membership = active_membership(session, organization_id, actor_user_id)
            require_permission(membership.role, Permission.EVENTS_CANCEL)
            event = session.scalar(
                select(EventModel)
                .where(
                    EventModel.id == event_id,
                    EventModel.organization_id == organization_id,
                )
                .with_for_update()
            )
            if event is None:
                raise ResourceNotFound("Event was not found.")
            if event.status == "cancelled":
                return event
            if event.status not in {"planning", "confirmed", "packed"}:
                raise StateConflict(
                    "Dispatched and returned events cannot be cancelled. Preserve their operational history."
                )

            released = self._release_allocation(session, event)
            event.status = "cancelled"
            event.version += 1
            data = dict(event.data or {})
            data["status"] = "cancelled"
            history = list(data.get("history", []))
            history.append(
                {
                    "action": "cancelled",
                    "actor_id": actor_user_id,
                    "note": "Event cancelled. Reserved inventory was released.",
                    "timestamp": utc_now().isoformat(),
                }
            )
            data["history"] = history
            event.data = data
            if released:
                session.add(
                    StockMovementModel(
                        organization_id=organization_id,
                        event_id=event.id,
                        action="cancelled",
                        idempotency_key=f"{event.id}:cancelled",
                        actor_membership_id=membership.id,
                        lines=released,
                    )
                )
            session.add(
                AuditEventModel(
                    organization_id=organization_id,
                    actor_membership_id=membership.id,
                    action="event.cancelled",
                    resource_type="event",
                    resource_id=event.id,
                    request_id=request_id,
                    changes={"released": released, "version": event.version},
                )
            )
            return event

    def delete_draft(
        self,
        organization_id: str,
        event_id: str,
        actor_user_id: str,
        request_id: str,
    ) -> None:
        with self.factory.begin() as session:
            membership = active_membership(session, organization_id, actor_user_id)
            require_permission(membership.role, Permission.EVENTS_DELETE)
            event = session.scalar(
                select(EventModel)
                .where(
                    EventModel.id == event_id,
                    EventModel.organization_id == organization_id,
                )
                .with_for_update()
            )
            if event is None:
                raise ResourceNotFound("Event was not found.")
            if event.status != "planning":
                raise StateConflict("Only an unconfirmed planning event can be deleted permanently.")
            if session.scalar(
                select(AllocationModel.id).where(
                    AllocationModel.organization_id == organization_id,
                    AllocationModel.event_id == event.id,
                )
            ) or session.scalar(
                select(StockMovementModel.id).where(
                    StockMovementModel.organization_id == organization_id,
                    StockMovementModel.event_id == event.id,
                )
            ):
                raise StateConflict("This event has operational history and cannot be deleted.")
            session.execute(
                delete(OperationRequestModel).where(
                    OperationRequestModel.organization_id == organization_id,
                    OperationRequestModel.resource_id == event.id,
                )
            )
            session.delete(event)
            session.add(
                AuditEventModel(
                    organization_id=organization_id,
                    actor_membership_id=membership.id,
                    action="event.deleted",
                    resource_type="event",
                    resource_id=event_id,
                    request_id=request_id,
                    changes={"status": "planning"},
                )
            )

    def _release_allocation(
        self, session: Session, event: EventModel
    ) -> list[dict[str, Any]]:
        allocation = session.scalar(
            select(AllocationModel)
            .where(
                AllocationModel.organization_id == event.organization_id,
                AllocationModel.event_id == event.id,
            )
            .with_for_update()
        )
        if allocation is None:
            return []
        if allocation.status not in {"reserved", "packed"}:
            raise StateConflict("This event allocation cannot be released safely.")
        lines = list(
            session.scalars(
                select(AllocationLineModel)
                .where(
                    AllocationLineModel.organization_id == event.organization_id,
                    AllocationLineModel.allocation_id == allocation.id,
                )
                .order_by(AllocationLineModel.holding_id)
                .with_for_update()
            )
        )
        holding_ids = [line.holding_id for line in lines]
        holdings = {
            holding.id: holding
            for holding in session.scalars(
                select(InventoryHoldingModel)
                .where(
                    InventoryHoldingModel.organization_id == event.organization_id,
                    InventoryHoldingModel.id.in_(holding_ids),
                )
                .order_by(InventoryHoldingModel.id)
                .with_for_update()
            )
        }
        bucket = f"{allocation.status}_quantity"
        released: list[dict[str, Any]] = []
        for line in lines:
            holding = holdings.get(line.holding_id)
            if holding is None or line.state != allocation.status:
                raise StateConflict("Event allocation is inconsistent and cannot be cancelled.")
            quantity = int(getattr(holding, bucket))
            if quantity < line.quantity:
                raise StateConflict("Event allocation quantity is inconsistent.")
            setattr(holding, bucket, quantity - line.quantity)
            holding.available_quantity += line.quantity
            holding.version += 1
            released.append(
                {
                    "scope": holding.scope,
                    "owner_user_id": holding.owner_user_id or "",
                    "item_id": holding.legacy_item_id,
                    "amount": line.quantity,
                    "from": allocation.status,
                    "to": "available",
                }
            )
        session.delete(allocation)
        return released

    def _reserve_plan(self, session: Session, event: EventModel):
        if event.data.get("plan_verified") is not True:
            raise StateConflict("Event plan must be regenerated by the server before reservation.")
        if session.scalar(
            select(AllocationModel).where(
                AllocationModel.event_id == event.id,
                AllocationModel.organization_id == event.organization_id,
            )
        ):
            raise StateConflict("Event already has an allocation.")
        plan_lines = list(event.data.get("plan", {}).get("lines", []))
        requested: dict[str, int] = {}
        for line in plan_lines:
            required_missing = int(
                line.get(
                    "required_missing",
                    line.get("missing", 0)
                    if line.get("level", "required") == "required"
                    else 0,
                )
            )
            if required_missing > 0:
                raise StateConflict("Event plan has missing required inventory.")
            item_id = str(line.get("item_id", "")).strip().lower()
            amount = int(line.get("amount", 0))
            if item_id and amount > 0:
                requested[item_id] = requested.get(item_id, 0) + amount

        allocation = AllocationModel(
            organization_id=event.organization_id,
            event_id=event.id,
            status="reserved",
        )
        session.add(allocation)
        session.flush()
        for legacy_item_id, amount in sorted(requested.items()):
            holdings = list(
                session.scalars(
                    select(InventoryHoldingModel)
                    .where(
                        InventoryHoldingModel.organization_id == event.organization_id,
                        InventoryHoldingModel.legacy_item_id == legacy_item_id,
                        InventoryHoldingModel.active.is_(True),
                        or_(
                            InventoryHoldingModel.scope == "shared",
                            and_(
                                InventoryHoldingModel.scope == "personal",
                                InventoryHoldingModel.owner_user_id == event.owner_user_id,
                            ),
                        ),
                    )
                    .order_by(
                        InventoryHoldingModel.scope,
                        InventoryHoldingModel.owner_user_id,
                        InventoryHoldingModel.id,
                    )
                    .with_for_update()
                )
            )
            remaining = amount
            for holding in holdings:
                take = min(remaining, holding.available_quantity)
                if take <= 0:
                    continue
                holding.available_quantity -= take
                holding.reserved_quantity += take
                holding.version += 1
                session.add(
                    AllocationLineModel(
                        organization_id=event.organization_id,
                        allocation_id=allocation.id,
                        holding_id=holding.id,
                        quantity=take,
                        state="reserved",
                    )
                )
                remaining -= take
                if remaining == 0:
                    break
            if remaining:
                raise StateConflict(
                    f"Not enough available stock for {legacy_item_id}; {remaining} missing."
                )

    def _move_allocation(
        self,
        session: Session,
        event: EventModel,
        source: str,
        target: str,
    ):
        allocation = session.scalar(
            select(AllocationModel)
            .where(
                AllocationModel.event_id == event.id,
                AllocationModel.organization_id == event.organization_id,
            )
            .with_for_update()
        )
        if allocation is None or allocation.status != source:
            raise StateConflict(f"Event allocation is not {source}.")
        lines = list(
            session.scalars(
                select(AllocationLineModel)
                .where(
                    AllocationLineModel.allocation_id == allocation.id,
                    AllocationLineModel.organization_id == event.organization_id,
                )
                .order_by(AllocationLineModel.holding_id)
                .with_for_update()
            )
        )
        holding_ids = [line.holding_id for line in lines]
        holdings = {
            holding.id: holding
            for holding in session.scalars(
                select(InventoryHoldingModel)
                .where(
                    InventoryHoldingModel.organization_id == event.organization_id,
                    InventoryHoldingModel.id.in_(holding_ids),
                )
                .order_by(InventoryHoldingModel.id)
                .with_for_update()
            )
        }
        source_column = f"{source}_quantity"
        target_column = "available_quantity" if target == "returned" else f"{target}_quantity"
        for line in lines:
            if line.state != source:
                raise StateConflict("Allocation line state does not match the event transition.")
            holding = holdings[line.holding_id]
            source_quantity = int(getattr(holding, source_column))
            if source_quantity < line.quantity:
                raise StateConflict("Inventory allocation quantity is inconsistent.")
            setattr(holding, source_column, source_quantity - line.quantity)
            setattr(
                holding,
                target_column,
                int(getattr(holding, target_column)) + line.quantity,
            )
            holding.version += 1
            line.state = target
        allocation.status = target
        allocation.version += 1

    def _movement_lines(
        self,
        session: Session,
        event: EventModel,
    ) -> list[dict[str, Any]]:
        allocation = session.scalar(
            select(AllocationModel).where(
                AllocationModel.event_id == event.id,
                AllocationModel.organization_id == event.organization_id,
            )
        )
        if allocation is None:
            return []
        return [
            {
                "holding_id": line.holding_id,
                "quantity": line.quantity,
                "state": line.state,
            }
            for line in session.scalars(
                select(AllocationLineModel).where(
                    AllocationLineModel.allocation_id == allocation.id,
                    AllocationLineModel.organization_id == event.organization_id,
                )
            )
        ]


class TransactionalInventoryOperations:
    """Atomic stock-definition and quantity reconciliation commands."""

    DEFINITION_UPDATE_OPERATION = "inventory.definition_update"
    ACTIVE_EDITABLE_FIELDS = frozenset({"info"})
    SEPARATE_COMMAND_FIELDS = frozenset(
        {
            "id",
            "count",
            "in_use_count",
            "available_quantity",
            "reserved_quantity",
            "packed_quantity",
            "dispatched_quantity",
            "scope",
            "owner_user_id",
            "active",
            "version",
        }
    )
    ADVANCED_DEFINITION_FIELDS = frozenset(
        {
            "attributes",
            "capabilities",
            "class_id",
            "connectors",
            "preference_score",
            "quality_score",
        }
    )

    def __init__(self, factory: sessionmaker[Session]):
        self.factory = factory

    def update_definition(
        self,
        organization_id: str,
        actor_user_id: str,
        scope: str,
        owner_user_id: str | None,
        item_id: str,
        new_item_id: str,
        metadata: dict[str, Any],
        idempotency_key: str,
        request_id: str,
    ) -> tuple[InventoryHoldingModel, bool]:
        self._validate_context(scope, owner_user_id, actor_user_id)
        self._validate_item_id(item_id)
        self._validate_item_id(new_item_id)
        self._validate_operation_key(idempotency_key)
        normalized_metadata = self._normalize_definition_metadata(
            metadata,
            reject_restricted=True,
        )
        payload = {
            "scope": scope,
            "owner_user_id": owner_user_id,
            "item_id": item_id,
            "new_item_id": new_item_id,
            "metadata": normalized_metadata,
        }
        fingerprint = self._fingerprint(payload)
        try:
            with self.factory.begin() as session:
                membership = self._authorize(
                    session,
                    organization_id,
                    actor_user_id,
                    scope,
                    import_required=False,
                    definition_required=True,
                )
                self._lock_organization(session, organization_id)
                existing = self._request(
                    session,
                    organization_id,
                    self.DEFINITION_UPDATE_OPERATION,
                    idempotency_key,
                    lock=True,
                )
                if existing is not None:
                    return self._existing_holding(session, existing, fingerprint), False

                request = OperationRequestModel(
                    organization_id=organization_id,
                    operation=self.DEFINITION_UPDATE_OPERATION,
                    idempotency_key=idempotency_key,
                    request_fingerprint=fingerprint,
                )
                session.add(request)
                session.flush()

                holding = self._holding(
                    session,
                    organization_id,
                    scope,
                    owner_user_id,
                    item_id,
                )
                if holding is None or not holding.active:
                    raise ResourceNotFound("Inventory item was not found.")
                if new_item_id != holding.legacy_item_id:
                    collision = self._holding(
                        session,
                        organization_id,
                        scope,
                        owner_user_id,
                        new_item_id,
                    )
                    if collision is not None:
                        raise StateConflict(
                            "Another inventory item already uses that identity."
                        )
                self._validate_dependency_metadata(
                    session,
                    organization_id,
                    scope,
                    owner_user_id,
                    new_item_id,
                    normalized_metadata,
                    replaced_item_id=holding.legacy_item_id,
                )

                before, after, changed_fields = self._definition_changes(
                    holding,
                    new_item_id,
                    normalized_metadata,
                )
                self._ensure_definition_change_allowed(holding, changed_fields)
                changed = bool(changed_fields)
                if changed:
                    holding.legacy_item_id = new_item_id
                    holding.data = normalized_metadata
                    holding.version += 1
                    self._audit_definition_update(
                        session,
                        holding,
                        membership.id,
                        request_id,
                        before,
                        after,
                        changed_fields,
                    )

                request.status = "completed"
                request.resource_id = holding.id
                request.response = {
                    "holding_id": holding.id,
                    "item_id": holding.legacy_item_id,
                    "changed": changed,
                }
                request.completed_at = utc_now()
                session.flush()
                return holding, changed
        except IntegrityError:
            with self.factory() as session:
                self._authorize(
                    session,
                    organization_id,
                    actor_user_id,
                    scope,
                    import_required=False,
                    definition_required=True,
                )
                existing = self._request(
                    session,
                    organization_id,
                    self.DEFINITION_UPDATE_OPERATION,
                    idempotency_key,
                    lock=False,
                )
                if existing is None:
                    raise
                return self._existing_holding(session, existing, fingerprint), False

    def adjust(
        self,
        organization_id: str,
        actor_user_id: str,
        scope: str,
        owner_user_id: str | None,
        item_id: str,
        amount: int,
        metadata: dict[str, Any],
        operation: str,
        idempotency_key: str,
        request_id: str,
        reason_code: str,
        reason: str,
        source: str,
        *,
        _trusted_preset: bool = False,
    ) -> tuple[InventoryHoldingModel, bool]:
        if operation not in {"inventory.add", "inventory.remove"}:
            raise ValueError("Inventory adjustment operation is invalid.")
        if amount <= 0:
            raise ValueError("Amount must be greater than zero.")
        self._validate_context(scope, owner_user_id, actor_user_id)
        self._validate_text(reason_code, reason, source)
        normalized_metadata = self._normalize_definition_metadata(
            metadata,
            reject_restricted=True,
        )
        payload = {
            "scope": scope,
            "owner_user_id": owner_user_id,
            "item_id": item_id,
            "amount": amount,
            "metadata": normalized_metadata if operation == "inventory.add" else {},
            "reason_code": reason_code,
            "reason": reason,
            "source": source,
        }
        fingerprint = self._fingerprint(payload)
        try:
            with self.factory.begin() as session:
                membership = self._authorize(
                    session, organization_id, actor_user_id, scope, import_required=False
                )
                self._lock_organization(session, organization_id)
                existing = self._request(
                    session, organization_id, operation, idempotency_key, lock=True
                )
                if existing is not None:
                    return self._existing_holding(session, existing, fingerprint), False
                request = OperationRequestModel(
                    organization_id=organization_id,
                    operation=operation,
                    idempotency_key=idempotency_key,
                    request_fingerprint=fingerprint,
                )
                session.add(request)
                session.flush()
                holding = self._holding(
                    session,
                    organization_id,
                    scope,
                    owner_user_id,
                    item_id,
                )
                definition_action = ""
                definition_update: tuple[
                    dict[str, Any],
                    dict[str, Any],
                    frozenset[str],
                ] | None = None
                if holding is None:
                    if operation == "inventory.remove":
                        raise ResourceNotFound("Inventory item was not found.")
                    holding = InventoryHoldingModel(
                        organization_id=organization_id,
                        legacy_item_id=item_id,
                        scope=scope,
                        owner_user_id=owner_user_id,
                        available_quantity=0,
                        active=True,
                        data=dict(normalized_metadata),
                    )
                    session.add(holding)
                    session.flush()
                    definition_action = "inventory.definition_created"
                    if self._advanced_metadata_fields(normalized_metadata) and not _trusted_preset:
                        require_permission(
                            membership.role,
                            Permission.INVENTORY_DEFINITION_MANAGE,
                        )
                    self._validate_dependency_metadata(
                        session,
                        organization_id,
                        scope,
                        owner_user_id,
                        item_id,
                        normalized_metadata,
                    )
                elif operation == "inventory.add":
                    merged_metadata = {
                        **self._normalize_definition_metadata(
                            dict(holding.data or {}),
                            reject_restricted=False,
                        ),
                        **dict(normalized_metadata),
                    }
                    before_definition, after_definition, changed_fields = (
                        self._definition_changes(
                            holding,
                            holding.legacy_item_id,
                            merged_metadata,
                        )
                    )
                    if changed_fields:
                        require_permission(
                            membership.role,
                            Permission.INVENTORY_DEFINITION_MANAGE,
                        )
                    if changed_fields or not holding.active:
                        self._validate_dependency_metadata(
                            session,
                            organization_id,
                            scope,
                            owner_user_id,
                            holding.legacy_item_id,
                            merged_metadata,
                        )
                    self._ensure_definition_change_allowed(holding, changed_fields)
                    if changed_fields:
                        holding.data = merged_metadata
                        definition_update = (
                            before_definition,
                            after_definition,
                            changed_fields,
                        )
                    if not holding.active:
                        holding.active = True
                        definition_action = "inventory.definition_reactivated"

                before = self._total(holding)
                if operation == "inventory.add":
                    holding.available_quantity += amount
                else:
                    if not holding.active:
                        raise ResourceNotFound("Inventory item was not found.")
                    if amount > holding.available_quantity:
                        raise StateConflict(
                            f"Cannot remove {amount}; only {holding.available_quantity} available."
                        )
                    if self._total(holding) == amount:
                        self._validate_dependency_graph(
                            session, organization_id, {},
                            removed_keys=[(scope, owner_user_id, item_id)],
                        )
                    holding.available_quantity -= amount
                    if self._total(holding) == 0:
                        holding.active = False
                        definition_action = "inventory.definition_archived"
                holding.version += 1
                after = self._total(holding)
                self._record_adjustment(
                    session,
                    holding,
                    membership.id,
                    operation,
                    before,
                    after,
                    reason_code,
                    reason,
                    source,
                    request_id,
                    idempotency_key,
                )
                if definition_action:
                    self._audit_definition(
                        session,
                        holding,
                        membership.id,
                        definition_action,
                        request_id,
                    )
                if definition_update is not None:
                    self._audit_definition_update(
                        session,
                        holding,
                        membership.id,
                        request_id,
                        *definition_update,
                    )
                request.status = "completed"
                request.resource_id = holding.id
                request.response = {
                    "holding_id": holding.id,
                    "item_id": holding.legacy_item_id,
                    "active": holding.active,
                }
                request.completed_at = utc_now()
                session.flush()
                return holding, True
        except IntegrityError:
            with self.factory() as session:
                self._authorize(
                    session, organization_id, actor_user_id, scope, import_required=False
                )
                existing = self._request(
                    session, organization_id, operation, idempotency_key, lock=False
                )
                if existing is None:
                    raise
                return self._existing_holding(session, existing, fingerprint), False

    def reconcile(
        self,
        organization_id: str,
        actor_user_id: str,
        scope: str,
        owner_user_id: str | None,
        items: list[dict[str, Any]],
        idempotency_key: str,
        request_id: str,
        reason_code: str,
        reason: str,
        source: str,
    ) -> tuple[int, bool]:
        self._validate_context(scope, owner_user_id, actor_user_id)
        self._validate_text(reason_code, reason, source)
        normalized = sorted(
            [
                {
                    **item,
                    "metadata": self._normalize_definition_metadata(
                        dict(item.get("metadata", {})),
                        reject_restricted=True,
                    ),
                }
                for item in items
            ],
            key=lambda item: str(item["item_id"]),
        )
        item_ids = [str(item["item_id"]) for item in normalized]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("Inventory reconciliation contains duplicate item IDs.")
        for item in normalized:
            if int(item["quantity"]) < 0:
                raise ValueError("Inventory quantity cannot be negative.")
        payload = {
            "scope": scope,
            "owner_user_id": owner_user_id,
            "items": normalized,
            "reason_code": reason_code,
            "reason": reason,
            "source": source,
        }
        fingerprint = self._fingerprint(payload)
        operation = "inventory.import"
        try:
            with self.factory.begin() as session:
                membership = self._authorize(
                    session, organization_id, actor_user_id, scope, import_required=True
                )
                self._lock_organization(session, organization_id)
                existing = self._request(
                    session, organization_id, operation, idempotency_key, lock=True
                )
                if existing is not None:
                    return self._existing_count(existing, fingerprint), False
                request = OperationRequestModel(
                    organization_id=organization_id,
                    operation=operation,
                    idempotency_key=idempotency_key,
                    request_fingerprint=fingerprint,
                )
                session.add(request)
                session.flush()
                dependency_updates = {
                    (scope, owner_user_id, str(item["item_id"])): tuple(
                        Requirement.from_dict(requirement)
                        for requirement in item["metadata"].get("requirements", [])
                    )
                    for item in normalized
                }
                validate_dependency_updates(
                    self._dependency_nodes(session, organization_id),
                    dependency_updates,
                )
                batch_id = new_id()
                for item in normalized:
                    item_id = str(item["item_id"])
                    metadata = dict(item.get("metadata", {}))
                    quantity = int(item["quantity"])
                    holding = self._holding(
                        session,
                        organization_id,
                        scope,
                        owner_user_id,
                        item_id,
                    )
                    definition_action = ""
                    definition_update: tuple[
                        dict[str, Any],
                        dict[str, Any],
                        frozenset[str],
                    ] | None = None
                    if holding is None:
                        holding = InventoryHoldingModel(
                            organization_id=organization_id,
                            legacy_item_id=item_id,
                            scope=scope,
                            owner_user_id=owner_user_id,
                            available_quantity=0,
                            active=True,
                            data=metadata,
                        )
                        session.add(holding)
                        session.flush()
                        definition_action = "inventory.definition_created"
                    elif not holding.active:
                        holding.active = True
                        definition_action = "inventory.definition_reactivated"

                    before = self._total(holding)
                    if definition_action == "inventory.definition_created":
                        metadata_changed = False
                    else:
                        before_definition, after_definition, changed_fields = (
                            self._definition_changes(
                                holding,
                                holding.legacy_item_id,
                                metadata,
                            )
                        )
                        self._ensure_definition_change_allowed(
                            holding,
                            changed_fields,
                        )
                        if changed_fields:
                            require_permission(
                                membership.role,
                                Permission.INVENTORY_DEFINITION_MANAGE,
                            )
                        metadata_changed = bool(changed_fields)
                        if metadata_changed:
                            definition_update = (
                                before_definition,
                                after_definition,
                                changed_fields,
                            )
                    quantity_changed = holding.available_quantity != quantity
                    if metadata_changed:
                        holding.data = metadata
                    holding.available_quantity = quantity
                    if metadata_changed or quantity_changed or definition_action:
                        holding.version += 1
                    after = self._total(holding)
                    if before != after:
                        self._record_adjustment(
                            session,
                            holding,
                            membership.id,
                            operation,
                            before,
                            after,
                            reason_code,
                            reason,
                            source,
                            request_id,
                            idempotency_key,
                            batch_id,
                        )
                    if definition_action:
                        self._audit_definition(
                            session,
                            holding,
                            membership.id,
                            definition_action,
                            request_id,
                            batch_id,
                        )
                    if definition_update is not None:
                        self._audit_definition_update(
                            session,
                            holding,
                            membership.id,
                            request_id,
                            *definition_update,
                            batch_id=batch_id,
                        )

                request.status = "completed"
                request.resource_id = batch_id
                request.response = {"imported": len(normalized), "batch_id": batch_id}
                request.completed_at = utc_now()
                session.flush()
                return len(normalized), True
        except IntegrityError:
            with self.factory() as session:
                self._authorize(
                    session, organization_id, actor_user_id, scope, import_required=True
                )
                existing = self._request(
                    session, organization_id, operation, idempotency_key, lock=False
                )
                if existing is None:
                    raise
                return self._existing_count(existing, fingerprint), False

    @staticmethod
    def _validate_context(
        scope: str,
        owner_user_id: str | None,
        actor_user_id: str,
    ) -> None:
        if scope not in {"shared", "personal"}:
            raise ValueError("Inventory scope must be shared or personal.")
        if scope == "shared" and owner_user_id is not None:
            raise ValueError("Shared inventory cannot have a personal owner.")
        if scope == "personal" and owner_user_id != actor_user_id:
            raise ResourceNotFound("Personal inventory was not found.")

    @staticmethod
    def _validate_text(reason_code: str, reason: str, source: str) -> None:
        if not reason_code or len(reason_code) > 64:
            raise ValueError("Inventory reason code is invalid.")
        if not reason or len(reason) > 300:
            raise ValueError("Inventory reason is invalid.")
        if not source or len(source) > 100:
            raise ValueError("Inventory source is invalid.")

    @staticmethod
    def _validate_item_id(item_id: str) -> None:
        if (
            not isinstance(item_id, str)
            or not item_id
            or item_id != item_id.strip().lower()
            or len(item_id) > 256
        ):
            raise ValueError("Inventory item identity is invalid.")

    @staticmethod
    def _validate_operation_key(idempotency_key: str) -> None:
        if (
            not isinstance(idempotency_key, str)
            or not idempotency_key
            or len(idempotency_key) > 160
        ):
            raise ValueError("Inventory operation key is invalid.")

    @classmethod
    def _normalize_definition_metadata(
        cls,
        metadata: dict[str, Any],
        *,
        reject_restricted: bool,
    ) -> dict[str, Any]:
        if not isinstance(metadata, dict):
            raise ValueError("Inventory definition metadata must be an object.")
        restricted = cls.SEPARATE_COMMAND_FIELDS.intersection(metadata)
        if reject_restricted and restricted:
            raise ValueError(
                "Stock quantities, ownership, scope, and holding state require a separate command."
            )
        normalized = {
            key: value
            for key, value in metadata.items()
            if key not in cls.SEPARATE_COMMAND_FIELDS
        }
        # Read-time defaults keep legacy definitions equivalent without rewriting stock.
        from catalog_terms import LEGACY_CODES
        normalized.setdefault("display_name", "")
        normalized.setdefault("category_label", "")
        normalized.setdefault("canonical_type", LEGACY_CODES.get(normalized.get("type"), normalized.get("type") or ""))
        normalized["requirements"] = [
            requirement.to_dict()
            for requirement in normalize_dependency_requirements(
                normalized.get("requirements", [])
            )
        ]
        return normalized

    @classmethod
    def _advanced_metadata_fields(cls, metadata: dict[str, Any]) -> frozenset[str]:
        fields: set[str] = set()
        for field in cls.ADVANCED_DEFINITION_FIELDS:
            value = metadata.get(field)
            if field in {"quality_score", "preference_score"}:
                if value not in {None, 0}:
                    fields.add(field)
            elif value not in (None, "", (), [], {}):
                fields.add(field)
        return frozenset(fields)

    @staticmethod
    def _dependency_nodes(
        session: Session,
        organization_id: str,
    ) -> list[DependencyNode]:
        rows = session.scalars(
            select(InventoryHoldingModel).where(
                InventoryHoldingModel.organization_id == organization_id,
                InventoryHoldingModel.active.is_(True),
            )
        )
        return [
            DependencyNode(
                scope=row.scope,
                owner_user_id=row.owner_user_id,
                item_id=row.legacy_item_id,
                requirements=normalize_dependency_requirements(
                    dict(row.data or {}).get("requirements", [])
                ),
            )
            for row in rows
        ]

    @classmethod
    def _validate_dependency_graph(
        cls, session: Session, organization_id: str,
        updates: dict[NodeKey, tuple[Requirement, ...]],
        *, removed_keys: tuple[NodeKey, ...] | list[NodeKey] = (),
    ) -> None:
        # Callers hold the organization lock until their entire command commits.
        # Include every owner's personal definitions; resolution still enforces scope.
        try:
            validate_dependency_updates(
                cls._dependency_nodes(session, organization_id), updates,
                removed_keys=removed_keys,
            )
        except DependencyTargetInUse as error:
            raise StateConflict(str(error)) from error

    @classmethod
    def _validate_dependency_metadata(
        cls,
        session: Session,
        organization_id: str,
        scope: str,
        owner_user_id: str | None,
        item_id: str,
        metadata: dict[str, Any],
        *,
        replaced_item_id: str | None = None,
    ) -> None:
        key: NodeKey = (scope, owner_user_id, item_id)
        removed = (
            [(scope, owner_user_id, replaced_item_id)]
            if replaced_item_id and replaced_item_id != item_id
            else []
        )
        cls._validate_dependency_graph(
            session, organization_id,
            {
                key: tuple(
                    Requirement.from_dict(requirement)
                    for requirement in metadata.get("requirements", [])
                )
            },
            removed_keys=removed,
        )

    @classmethod
    def _definition_changes(
        cls,
        holding: InventoryHoldingModel,
        new_item_id: str,
        new_metadata: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], frozenset[str]]:
        before = {
            "item_id": holding.legacy_item_id,
            **cls._normalize_definition_metadata(
                dict(holding.data or {}),
                reject_restricted=False,
            ),
        }
        after = {"item_id": new_item_id, **dict(new_metadata)}
        changed_fields = frozenset(
            field
            for field in before.keys() | after.keys()
            if before.get(field) != after.get(field)
        )
        return before, after, changed_fields

    @classmethod
    def _ensure_definition_change_allowed(
        cls,
        holding: InventoryHoldingModel,
        changed_fields: frozenset[str],
    ) -> None:
        protected_changes = changed_fields - cls.ACTIVE_EDITABLE_FIELDS
        operational_quantity = sum(
            (
                holding.reserved_quantity,
                holding.packed_quantity,
                holding.dispatched_quantity,
            )
        )
        if operational_quantity and protected_changes:
            raise StateConflict(
                "This inventory definition cannot change while stock is reserved, packed, or dispatched."
            )

    @staticmethod
    def _authorize(
        session: Session,
        organization_id: str,
        actor_user_id: str,
        scope: str,
        *,
        import_required: bool,
        definition_required: bool = False,
    ) -> MembershipModel:
        membership = active_membership(session, organization_id, actor_user_id)
        permission = (
            Permission.INVENTORY_PERSONAL_WRITE
            if scope == "personal"
            else Permission.INVENTORY_SHARED_WRITE
        )
        require_permission(membership.role, permission)
        if import_required:
            require_permission(membership.role, Permission.INVENTORY_IMPORT)
        if definition_required:
            require_permission(
                membership.role,
                Permission.INVENTORY_DEFINITION_MANAGE,
            )
        return membership

    @staticmethod
    def _lock_organization(session: Session, organization_id: str, *, no_key_update: bool = False) -> None:
        organization = session.scalar(
            select(OrganizationModel)
            .where(OrganizationModel.id == organization_id)
            .with_for_update(key_share=no_key_update)
        )
        if organization is None:
            raise ResourceNotFound("Organization was not found.")

    @staticmethod
    def _holding(
        session: Session,
        organization_id: str,
        scope: str,
        owner_user_id: str | None,
        item_id: str,
    ) -> InventoryHoldingModel | None:
        owner_filter = (
            InventoryHoldingModel.owner_user_id.is_(None)
            if owner_user_id is None
            else InventoryHoldingModel.owner_user_id == owner_user_id
        )
        return session.scalar(
            select(InventoryHoldingModel)
            .where(
                InventoryHoldingModel.organization_id == organization_id,
                InventoryHoldingModel.scope == scope,
                owner_filter,
                InventoryHoldingModel.legacy_item_id == item_id,
            )
            .with_for_update()
        )

    @staticmethod
    def _total(holding: InventoryHoldingModel) -> int:
        return sum(
            (
                holding.available_quantity,
                holding.reserved_quantity,
                holding.packed_quantity,
                holding.dispatched_quantity,
            )
        )

    @staticmethod
    def _fingerprint(payload: dict[str, Any]) -> str:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _request(
        session: Session,
        organization_id: str,
        operation: str,
        idempotency_key: str,
        *,
        lock: bool,
    ) -> OperationRequestModel | None:
        statement = select(OperationRequestModel).where(
            OperationRequestModel.organization_id == organization_id,
            OperationRequestModel.operation == operation,
            OperationRequestModel.idempotency_key == idempotency_key,
        )
        if lock:
            statement = statement.with_for_update()
        return session.scalar(statement)

    @staticmethod
    def _existing_holding(
        session: Session,
        request: OperationRequestModel,
        fingerprint: str,
    ) -> InventoryHoldingModel:
        if request.request_fingerprint != fingerprint:
            raise StateConflict(
                "This idempotency key was already used for another inventory change."
            )
        if request.status != "completed" or not request.resource_id:
            raise StateConflict("This inventory change is still being processed.")
        holding = session.scalar(
            select(InventoryHoldingModel).where(
                InventoryHoldingModel.organization_id == request.organization_id,
                InventoryHoldingModel.id == request.resource_id,
            )
        )
        if holding is None:
            raise StateConflict("The completed inventory change is unavailable.")
        return holding

    @staticmethod
    def _existing_count(request: OperationRequestModel, fingerprint: str) -> int:
        if request.request_fingerprint != fingerprint:
            raise StateConflict(
                "This idempotency key was already used for another inventory import."
            )
        if request.status != "completed":
            raise StateConflict("This inventory import is still being processed.")
        return int(dict(request.response or {}).get("imported", 0))

    @staticmethod
    def _record_adjustment(
        session: Session,
        holding: InventoryHoldingModel,
        actor_membership_id: str,
        operation: str,
        before: int,
        after: int,
        reason_code: str,
        reason: str,
        source: str,
        request_id: str,
        idempotency_key: str,
        batch_id: str | None = None,
    ) -> None:
        adjustment = InventoryAdjustmentModel(
            organization_id=holding.organization_id,
            holding_id=holding.id,
            item_id=holding.legacy_item_id,
            actor_membership_id=actor_membership_id,
            operation=operation,
            before_quantity=before,
            after_quantity=after,
            delta=after - before,
            reason_code=reason_code,
            reason=reason,
            source=source,
            request_id=request_id,
            idempotency_key=idempotency_key,
            batch_id=batch_id,
        )
        session.add(adjustment)
        session.flush()
        session.add(
            AuditEventModel(
                organization_id=holding.organization_id,
                actor_membership_id=actor_membership_id,
                action="inventory.adjusted",
                resource_type="inventory_holding",
                resource_id=holding.id,
                request_id=request_id,
                changes={
                    "adjustment_id": adjustment.id,
                    "operation": operation,
                    "item_id": holding.legacy_item_id,
                    "before_quantity": before,
                    "after_quantity": after,
                    "delta": after - before,
                    "reason_code": reason_code,
                    "source": source,
                    "batch_id": batch_id,
                },
            )
        )

    @staticmethod
    def _audit_definition(
        session: Session,
        holding: InventoryHoldingModel,
        actor_membership_id: str,
        action: str,
        request_id: str,
        batch_id: str | None = None,
    ) -> None:
        session.add(
            AuditEventModel(
                organization_id=holding.organization_id,
                actor_membership_id=actor_membership_id,
                action=action,
                resource_type="inventory_holding",
                resource_id=holding.id,
                request_id=request_id,
                changes={
                    "item_id": holding.legacy_item_id,
                    "active": holding.active,
                    "batch_id": batch_id,
                },
            )
        )

    @staticmethod
    def _audit_definition_update(
        session: Session,
        holding: InventoryHoldingModel,
        actor_membership_id: str,
        request_id: str,
        before: dict[str, Any],
        after: dict[str, Any],
        changed_fields: frozenset[str],
        batch_id: str | None = None,
    ) -> None:
        ordered_fields = sorted(changed_fields)
        session.add(
            AuditEventModel(
                organization_id=holding.organization_id,
                actor_membership_id=actor_membership_id,
                action="inventory.definition_updated",
                resource_type="inventory_holding",
                resource_id=holding.id,
                request_id=request_id,
                changes={
                    "changed_fields": ordered_fields,
                    "before": {field: before.get(field) for field in ordered_fields},
                    "after": {field: after.get(field) for field in ordered_fields},
                    "batch_id": batch_id,
                },
            )
        )


class TransactionalKitOperations:
    """Create, allocate, pack, and dispatch a kit in one database transaction."""

    OPERATION = "kit.checkout"

    def __init__(self, factory: sessionmaker[Session]):
        self.factory = factory
        self.events = TransactionalEventOperations(factory)

    def checkout(
        self,
        organization_id: str,
        owner_user_id: str,
        actor_membership_id: str,
        source_id: str,
        idempotency_key: str,
        request_id: str,
        event_data_factory: Callable[[], dict[str, Any]],
    ) -> tuple[EventModel, bool]:
        source_id = source_id.strip().lower()
        idempotency_key = idempotency_key.strip()
        if not organization_id or not owner_user_id or not source_id:
            raise ValueError("Kit checkout context is incomplete.")
        if not idempotency_key or len(idempotency_key) > 160:
            raise ValueError("Kit checkout idempotency key is invalid.")
        fingerprint = hashlib.sha256(
            json.dumps(
                {"source_id": source_id},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        try:
            with self.factory.begin() as session:
                existing = self._request(
                    session, organization_id, idempotency_key, lock=True
                )
                if existing is not None:
                    return self._existing_result(session, existing, fingerprint)

                legacy = self._legacy_result(
                    session, organization_id, idempotency_key, source_id
                )
                if legacy is not None:
                    return legacy, False

                operation = OperationRequestModel(
                    organization_id=organization_id,
                    operation=self.OPERATION,
                    idempotency_key=idempotency_key,
                    request_fingerprint=fingerprint,
                )
                session.add(operation)
                session.flush()

                event_data = event_data_factory()
                if (
                    event_data.get("source_type") != "kit"
                    or event_data.get("source_id") != source_id
                ):
                    raise ValueError("Kit checkout source is invalid.")
                event_id = new_id()
                authoritative_data = dict(event_data)
                authoritative_data.update(
                    {
                        "id": event_id,
                        "organization_id": organization_id,
                        "owner_id": owner_user_id,
                        "assigned_user_ids": [owner_user_id],
                        "source_type": "kit",
                        "source_id": source_id,
                        "status": "planning",
                        "plan_verified": True,
                        "movements": [],
                    }
                )
                event = EventModel(
                    id=event_id,
                    organization_id=organization_id,
                    owner_user_id=owner_user_id,
                    title=str(authoritative_data.get("title", "Kit checkout")),
                    status="planning",
                    priority_score=int(authoritative_data.get("priority_score", 50)),
                    data=authoritative_data,
                )
                session.add(event)
                session.flush()

                self.events.transition_in_session(
                    session,
                    organization_id,
                    event.id,
                    "confirmed",
                    actor_membership_id,
                    request_id,
                )
                packed_data = dict(event.data or {})
                packed_data["checklist"] = [
                    {**item, "done": True}
                    for item in packed_data.get("checklist", [])
                ]
                packed_history = list(packed_data.get("history", []))
                packed_history.append(
                    {
                        "action": "checklist",
                        "actor_id": actor_membership_id,
                        "note": "Kit packing checklist completed automatically.",
                        "timestamp": utc_now().isoformat(),
                    }
                )
                packed_data["history"] = packed_history
                event.data = packed_data
                self.events.transition_in_session(
                    session,
                    organization_id,
                    event.id,
                    "packed",
                    actor_membership_id,
                    request_id,
                )
                self.events.transition_in_session(
                    session,
                    organization_id,
                    event.id,
                    "out",
                    actor_membership_id,
                    request_id,
                    f"kit:{idempotency_key}",
                )

                operation.status = "completed"
                operation.resource_id = event.id
                operation.response = {"event_id": event.id, "source_id": source_id}
                operation.completed_at = utc_now()
                session.add(
                    AuditEventModel(
                        organization_id=organization_id,
                        actor_membership_id=actor_membership_id,
                        action="kit.checkout",
                        resource_type="event",
                        resource_id=event.id,
                        request_id=request_id,
                        changes={
                            "source_id": source_id,
                            "idempotency_key": idempotency_key,
                        },
                    )
                )
                session.flush()
                return event, True
        except IntegrityError:
            with self.factory() as session:
                existing = self._request(
                    session, organization_id, idempotency_key, lock=False
                )
                if existing is None:
                    raise
                return self._existing_result(session, existing, fingerprint)

    def _request(
        self,
        session: Session,
        organization_id: str,
        idempotency_key: str,
        *,
        lock: bool,
    ) -> OperationRequestModel | None:
        statement = select(OperationRequestModel).where(
            OperationRequestModel.organization_id == organization_id,
            OperationRequestModel.operation == self.OPERATION,
            OperationRequestModel.idempotency_key == idempotency_key,
        )
        if lock:
            statement = statement.with_for_update()
        return session.scalar(statement)

    def _existing_result(
        self,
        session: Session,
        request: OperationRequestModel,
        fingerprint: str,
    ) -> tuple[EventModel, bool]:
        if request.request_fingerprint != fingerprint:
            raise StateConflict(
                "This idempotency key was already used for another kit checkout."
            )
        if request.status != "completed" or not request.resource_id:
            raise StateConflict("This kit checkout is still being processed.")
        event = session.scalar(
            select(EventModel).where(
                EventModel.id == request.resource_id,
                EventModel.organization_id == request.organization_id,
            )
        )
        if event is None:
            raise StateConflict("The completed kit checkout event is unavailable.")
        return event, False

    def _legacy_result(
        self,
        session: Session,
        organization_id: str,
        idempotency_key: str,
        source_id: str,
    ) -> EventModel | None:
        movement = session.scalar(
            select(StockMovementModel).where(
                StockMovementModel.organization_id == organization_id,
                StockMovementModel.idempotency_key == f"kit:{idempotency_key}",
            )
        )
        if movement is None:
            return None
        event = session.scalar(
            select(EventModel).where(
                EventModel.id == movement.event_id,
                EventModel.organization_id == organization_id,
            )
        )
        if event is None or event.data.get("source_id") != source_id:
            raise StateConflict(
                "This idempotency key was already used for another kit checkout."
            )
        return event
