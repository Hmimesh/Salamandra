from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from event_operations import EVENT_TRANSITIONS
from security import ResourceNotFound, StateConflict


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
            "status IN ('planning', 'confirmed', 'packed', 'out', 'returned')",
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
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


def create_database_engine(database_url: str, *, production: bool = False) -> Engine:
    if production and not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise ValueError("Production persistence requires PostgreSQL.")
    return create_engine(database_url, future=True, pool_pre_ping=True)


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


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
    ) -> EventModel:
        with self.factory.begin() as session:
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
            movement = StockMovementModel(
                organization_id=organization_id,
                event_id=event.id,
                action=next_status,
                idempotency_key=f"{event.id}:{next_status}",
                actor_membership_id=actor_membership_id,
                lines=self._movement_lines(session, event),
            )
            session.add(movement)
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
                    )
                    .order_by(
                        InventoryHoldingModel.scope.desc(),
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
