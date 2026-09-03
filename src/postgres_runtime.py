from __future__ import annotations

import hashlib
import os
import secrets
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import and_, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from accounts import AccountStore, UserAccount, default_preferences
from database import (
    AuditEventModel,
    EventModel,
    IntegrationConnectionModel,
    InventoryHoldingModel,
    ItemClassRecordModel,
    MembershipModel,
    OrganizationModel,
    SessionModel,
    StockMovementModel,
    TransactionalEventDetails,
    TransactionalEventOperations,
    TransactionalInventoryOperations,
    TransactionalKitOperations,
    UserModel,
    new_id,
    utc_now,
)
from event_memory import EventRecord
from integrations import DEFAULT_INTEGRATIONS, IntegrationStore
from Inventory import Inventory
from inventory_workspace import PERSONAL_SCOPE, SHARED_SCOPE
from Item_node import ItemNode, Requirement, normalize_item_id
from item_classes import ConfiguredItemClass, ItemClassCatalog
from security import ResourceNotFound, StateConflict


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _event_start(event: EventRecord) -> datetime | None:
    try:
        return datetime.fromisoformat(f"{event.start_date}T{event.start_time}:00").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


class PostgresAccountStore:
    allow_demo = False

    def __init__(self, factory: sessionmaker[Session]):
        self.factory = factory

    def authenticate(self, email: str, password: str) -> UserAccount | None:
        normalized = str(email).strip().lower()
        with self.factory.begin() as session:
            row = session.scalar(
                select(UserModel).where(
                    func.lower(UserModel.email) == normalized,
                    UserModel.status == "active",
                )
            )
            if row is None or not AccountStore.verify_password(password, row.password_hash):
                return None
            if not row.password_hash.startswith("$argon2"):
                row.password_hash = AccountStore.hash_password(password)
            membership = self._membership(session, row.id)
            return self._account(session, row, membership) if membership else None

    def get(self, user_id: str) -> UserAccount | None:
        with self.factory() as session:
            row = session.get(UserModel, user_id)
            membership = self._membership(session, user_id) if row else None
            return self._account(session, row, membership) if row and membership else None

    def get_for_membership(
        self,
        membership_id: str,
        organization_id: str,
        user_id: str | None = None,
    ) -> UserAccount | None:
        with self.factory() as session:
            membership = session.scalar(
                select(MembershipModel).where(
                    MembershipModel.id == membership_id,
                    MembershipModel.organization_id == organization_id,
                    MembershipModel.status == "active",
                )
            )
            if membership is not None and user_id and membership.user_id != user_id:
                return None
            row = session.get(UserModel, membership.user_id) if membership else None
            if row is None or row.status != "active":
                return None
            return self._account(session, row, membership)

    def membership_id(self, user: UserAccount) -> str:
        with self.factory() as session:
            membership = session.scalar(
                select(MembershipModel).where(
                    MembershipModel.user_id == user.id,
                    MembershipModel.organization_id == user.organization_id,
                    MembershipModel.status == "active",
                )
            )
            if membership is None:
                raise ResourceNotFound("Organization membership was not found.")
            return membership.id

    def list_organization_users(self, organization_id: str) -> list[UserAccount]:
        with self.factory() as session:
            rows = session.execute(
                select(UserModel, MembershipModel)
                .join(MembershipModel, MembershipModel.user_id == UserModel.id)
                .where(
                    MembershipModel.organization_id == organization_id,
                    MembershipModel.status == "active",
                    UserModel.status == "active",
                )
                .order_by(UserModel.name, UserModel.id)
            )
            return [self._account(session, user, membership) for user, membership in rows]

    def create_user(
        self,
        name: str,
        email: str,
        password: str,
        role: str,
        organization_id: str,
        organization_name: str,
        title: str = "Event Operations",
        warehouse: str = "Main Warehouse",
    ) -> UserAccount:
        normalized = email.strip().lower()
        if not name.strip():
            raise ValueError("Team member name is required.")
        if "@" not in normalized:
            raise ValueError("A valid team member email is required.")
        if len(password) < 6:
            raise ValueError("Temporary password must be at least 6 characters.")
        with self.factory.begin() as session:
            if session.scalar(select(UserModel.id).where(func.lower(UserModel.email) == normalized)):
                raise ValueError("An account with this email already exists.")
            organization = session.get(OrganizationModel, organization_id)
            if organization is None:
                organization = OrganizationModel(id=organization_id, name=organization_name)
                session.add(organization)
            user = UserModel(
                id=new_id(),
                email=normalized,
                name=name.strip(),
                password_hash=AccountStore.hash_password(password),
                preferences={
                    **default_preferences(),
                    "_profile": {
                        "title": title.strip() or "Event Operations",
                        "warehouse": warehouse.strip() or "Main Warehouse",
                        "avatar_url": "",
                    },
                },
            )
            session.add(user)
            session.flush()
            membership = MembershipModel(
                organization_id=organization_id,
                user_id=user.id,
                role=role.strip().lower() or "operator",
            )
            session.add(membership)
            session.flush()
            return self._account(session, user, membership)

    def register_workspace(
        self,
        *,
        name: str,
        email: str,
        password: str,
        organization_name: str,
    ) -> UserAccount:
        display_name = str(name).strip()
        normalized_email = str(email).strip().casefold()
        workspace_name = str(organization_name).strip()
        if not display_name or len(display_name) > 200:
            raise ValueError("Name must be between 1 and 200 characters.")
        if (
            not normalized_email
            or len(normalized_email) > 320
            or normalized_email.count("@") != 1
            or any(character.isspace() for character in normalized_email)
        ):
            raise ValueError("Enter a valid work email address.")
        local_part, domain = normalized_email.rsplit("@", 1)
        if (
            not local_part
            or "." not in domain
            or domain.startswith(".")
            or domain.endswith(".")
        ):
            raise ValueError("Enter a valid work email address.")
        if len(password) < 8 or len(password) > 256:
            raise ValueError("Password must be between 8 and 256 characters.")
        if not workspace_name or len(workspace_name) > 200:
            raise ValueError("Workspace name must be between 1 and 200 characters.")

        user_id = new_id()
        organization_id = new_id()
        password_hash = AccountStore.hash_password(password)
        try:
            with self.factory.begin() as session:
                organization = OrganizationModel(
                    id=organization_id,
                    name=workspace_name,
                )
                user = UserModel(
                    id=user_id,
                    email=normalized_email,
                    name=display_name,
                    password_hash=password_hash,
                    preferences={
                        **default_preferences(),
                        "_profile": {
                            "title": "Workspace Owner",
                            "warehouse": "Main Warehouse",
                            "avatar_url": "",
                        },
                    },
                )
                membership = MembershipModel(
                    id=new_id(),
                    organization_id=organization_id,
                    user_id=user_id,
                    role="owner",
                )
                session.add(organization)
                session.flush()
                session.add(user)
                session.flush()
                session.add(membership)
                session.flush()
                return self._account(session, user, membership)
        except IntegrityError as error:
            raise StateConflict(
                "A workspace could not be created with these details. "
                "Try signing in or use another work email."
            ) from error

    def update_profile(self, user_id: str, *, name: str, title: str, warehouse: str) -> UserAccount:
        if not name.strip():
            raise ValueError("Name is required.")
        with self.factory.begin() as session:
            user = session.get(UserModel, user_id)
            if user is None:
                raise ValueError("Account was not found.")
            membership = self._membership(session, user_id)
            if membership is None:
                raise ValueError("Account membership was not found.")
            preferences = dict(user.preferences or {})
            profile = dict(preferences.get("_profile", {}))
            user.name = name.strip()
            profile["title"] = title.strip() or profile.get("title", "Event Operations")
            profile["warehouse"] = warehouse.strip() or profile.get("warehouse", "Main Warehouse")
            preferences["_profile"] = profile
            user.preferences = preferences
            return self._account(session, user, membership)

    def update_preferences(self, user_id: str, **values: Any) -> UserAccount:
        allowed = {
            "theme": {"system", "light", "dark"},
            "font_scale": {"compact", "comfortable", "large"},
            "density": {"compact", "comfortable"},
        }
        with self.factory.begin() as session:
            user = session.get(UserModel, user_id)
            if user is None:
                raise ValueError("Account was not found.")
            membership = self._membership(session, user_id)
            preferences = {**default_preferences(), **dict(user.preferences or {})}
            for name, choices in allowed.items():
                if name in values:
                    value = str(values[name]).strip().lower()
                    if value not in choices:
                        raise ValueError(f"Invalid {name.replace('_', ' ')} preference.")
                    preferences[name] = value
            if "show_progress" in values:
                preferences["show_progress"] = bool(values["show_progress"])
            if "onboarding_dismissed" in values:
                preferences["onboarding_dismissed"] = bool(
                    values["onboarding_dismissed"]
                )
            user.preferences = preferences
            return self._account(session, user, membership)

    def _membership(self, session: Session, user_id: str) -> MembershipModel | None:
        return session.scalar(
            select(MembershipModel)
            .where(
                MembershipModel.user_id == user_id,
                MembershipModel.status == "active",
            )
            .order_by(MembershipModel.organization_id, MembershipModel.id)
        )

    def _account(
        self, session: Session, user: UserModel, membership: MembershipModel
    ) -> UserAccount:
        organization = session.get(OrganizationModel, membership.organization_id)
        preferences = {**default_preferences(), **dict(user.preferences or {})}
        profile = dict(preferences.pop("_profile", {}))
        return UserAccount(
            id=user.id,
            name=user.name,
            email=user.email,
            role=membership.role,
            organization_id=membership.organization_id,
            organization_name=organization.name if organization else "Salamandra",
            title=profile.get("title", "Event Operations"),
            warehouse=profile.get("warehouse", "Main Warehouse"),
            avatar_url=profile.get("avatar_url", ""),
            password_hash=user.password_hash,
            preferences=preferences,
        )


class PostgresInventoryWorkspace:
    def __init__(self, factory: sessionmaker[Session]):
        self.factory = factory
        self.adjustments = TransactionalInventoryOperations(factory)

    def inventory_for(
        self, scope: str, user_id: str | None = None, organization_id: str = "salamandra"
    ) -> Inventory:
        return self._inventory(scope, user_id, organization_id)

    def combined_inventory(
        self, user_id: str | None = None, organization_id: str = "salamandra"
    ) -> Inventory:
        combined = Inventory()
        for scope, owner in ((SHARED_SCOPE, None), (PERSONAL_SCOPE, user_id)):
            if scope == PERSONAL_SCOPE and not owner:
                continue
            for item in self._inventory(scope, owner, organization_id).list_items():
                existing = combined.get_item(item.id)
                if existing is None:
                    combined.items[item.id] = ItemNode.from_dict(item.to_dict())
                else:
                    existing.count += item.count
                    existing.in_use_count += item.in_use_count
        return combined

    def add_item(
        self,
        item: ItemNode,
        amount: int,
        scope: str,
        user_id: str | None,
        organization_id: str = "salamandra",
        *,
        request_id: str = "",
        reason_code: str = "stock_received",
        reason: str = "Inventory received.",
        source: str = "inventory_ui",
    ) -> ItemNode:
        owner = self._owner(scope, user_id)
        item_id = normalize_item_id(item.id)
        operation_key = request_id or new_id()
        row, _ = self.adjustments.adjust(
            organization_id,
            user_id or "",
            scope,
            owner,
            item_id,
            amount,
            self._metadata(item),
            "inventory.add",
            operation_key,
            operation_key,
            reason_code,
            reason,
            source,
        )
        return self._item(row)

    def add_from_preset(
        self,
        preset,
        scope: str,
        user_id: str | None,
        amount: int | None = None,
        organization_id: str = "salamandra",
        *,
        request_id: str = "",
    ) -> ItemNode:
        item = preset.create_item(count=amount)
        return self.add_item(
            item,
            item.count,
            scope,
            user_id,
            organization_id,
            request_id=request_id,
            reason_code="preset_received",
            reason=f"Stock added from preset {preset.id}.",
            source="inventory_preset",
        )

    def update_item(
        self,
        item_id: str,
        updated_item: ItemNode,
        scope: str,
        user_id: str | None,
        organization_id: str = "salamandra",
        *,
        request_id: str = "",
    ) -> ItemNode:
        owner = self._owner(scope, user_id)
        operation_key = request_id or new_id()
        row, _ = self.adjustments.update_definition(
            organization_id=organization_id,
            actor_user_id=user_id or "",
            scope=scope,
            owner_user_id=owner,
            item_id=normalize_item_id(item_id),
            new_item_id=normalize_item_id(updated_item.id),
            metadata=self._metadata(updated_item),
            idempotency_key=operation_key,
            request_id=operation_key,
        )
        return self._item(row)

    def use_item(self, item_id: str, amount: int, scope: str, user_id: str | None, organization_id: str = "salamandra") -> bool:
        raise StateConflict(
            "Direct inventory checkout is disabled in the PostgreSQL runtime."
        )

    def return_item(self, item_id: str, amount: int, scope: str, user_id: str | None, organization_id: str = "salamandra") -> bool:
        raise StateConflict(
            "Direct inventory return is disabled in the PostgreSQL runtime."
        )

    def remove_item(
        self,
        item_id: str,
        amount: int,
        scope: str,
        user_id: str | None,
        organization_id: str = "salamandra",
        *,
        request_id: str = "",
        reason_code: str = "stock_removed",
        reason: str = "Inventory removed.",
        source: str = "inventory_ui",
    ) -> bool:
        owner = self._owner(scope, user_id)
        operation_key = request_id or new_id()
        self.adjustments.adjust(
            organization_id,
            user_id or "",
            scope,
            owner,
            normalize_item_id(item_id),
            amount,
            {},
            "inventory.remove",
            operation_key,
            operation_key,
            reason_code,
            reason,
            source,
        )
        return True

    def replace_items(
        self,
        items: list[ItemNode],
        scope: str,
        user_id: str | None,
        organization_id: str,
        *,
        request_id: str = "",
        reason_code: str = "csv_reconciliation",
        reason: str = "Inventory reconciled from CSV.",
        source: str = "csv_import",
    ) -> None:
        owner = self._owner(scope, user_id)
        operation_key = request_id or new_id()
        self.adjustments.reconcile(
            organization_id,
            user_id or "",
            scope,
            owner,
            [
                {
                    "item_id": normalize_item_id(item.id),
                    "quantity": item.count,
                    "metadata": self._metadata(item),
                }
                for item in items
            ],
            operation_key,
            operation_key,
            reason_code,
            reason,
            source,
        )

    def to_dict(self, user_id: str | None = None, organization_id: str | None = None) -> dict[str, Any]:
        if not organization_id:
            empty = Inventory().to_dict()
            return {"shared": empty, "personal": empty, "combined": empty}
        shared = self._inventory(SHARED_SCOPE, None, organization_id)
        personal = self._inventory(PERSONAL_SCOPE, user_id, organization_id) if user_id else Inventory()
        return {
            "shared": shared.to_dict(),
            "personal": personal.to_dict(),
            "combined": self.combined_inventory(user_id, organization_id).to_dict(),
        }

    def save(self) -> None:
        return

    def migrate_preset_metadata(self, catalog) -> bool:
        return False

    def _inventory(self, scope: str, owner: str | None, organization_id: str) -> Inventory:
        owner = self._owner(scope, owner)
        with self.factory() as session:
            rows = session.scalars(
                select(InventoryHoldingModel)
                .where(
                    InventoryHoldingModel.organization_id == organization_id,
                    InventoryHoldingModel.scope == scope,
                    InventoryHoldingModel.active.is_(True),
                    InventoryHoldingModel.owner_user_id.is_(None) if owner is None else InventoryHoldingModel.owner_user_id == owner,
                )
                .order_by(InventoryHoldingModel.legacy_item_id, InventoryHoldingModel.id)
            )
            inventory = Inventory()
            for row in rows:
                item = self._item(row)
                inventory.items[item.id] = item
            return inventory

    def _holding(self, session: Session, organization_id: str, scope: str, owner: str | None, item_id: str, lock: bool = False) -> InventoryHoldingModel | None:
        statement = select(InventoryHoldingModel).where(
            InventoryHoldingModel.organization_id == organization_id,
            InventoryHoldingModel.scope == scope,
            InventoryHoldingModel.legacy_item_id == item_id,
            InventoryHoldingModel.owner_user_id.is_(None) if owner is None else InventoryHoldingModel.owner_user_id == owner,
        )
        if lock:
            statement = statement.with_for_update()
        return session.scalar(statement)

    @staticmethod
    def _owner(scope: str, user_id: str | None) -> str | None:
        if scope not in {SHARED_SCOPE, PERSONAL_SCOPE}:
            raise ValueError("Inventory scope must be shared or personal.")
        if scope == PERSONAL_SCOPE:
            if not user_id:
                raise ValueError("Personal inventory requires a signed-in account.")
            return user_id
        return None

    @staticmethod
    def _metadata(item: ItemNode) -> dict[str, Any]:
        data = item.to_dict()
        data.pop("id", None)
        data.pop("count", None)
        data.pop("in_use_count", None)
        return data

    @staticmethod
    def _item(row: InventoryHoldingModel) -> ItemNode:
        return ItemNode.from_dict(
            {
                **dict(row.data or {}),
                "id": row.legacy_item_id,
                "count": row.available_quantity,
                "in_use_count": row.dispatched_quantity,
            }
        )


class PostgresEventMemory:
    def __init__(self, factory: sessionmaker[Session]):
        self.factory = factory

    def add(self, event: EventRecord) -> None:
        starts_at = _event_start(event)
        with self.factory.begin() as session:
            row = session.scalar(
                select(EventModel).where(
                    EventModel.id == event.id,
                    EventModel.organization_id == event.organization_id,
                ).with_for_update()
            )
            data = {**event.to_dict(), "plan_verified": True}
            if row is None:
                row = EventModel(
                    id=event.id,
                    organization_id=event.organization_id,
                    owner_user_id=event.owner_id,
                    title=event.title,
                    status=event.status,
                    starts_at=starts_at,
                    ends_at=starts_at + timedelta(minutes=event.duration_minutes) if starts_at else None,
                    priority_score=event.priority_score,
                    data=data,
                )
                session.add(row)
            else:
                raise StateConflict(
                    "Existing PostgreSQL events must be changed through an explicit command."
                )

    def get(self, event_id: str) -> EventRecord | None:
        with self.factory() as session:
            row = session.get(EventModel, event_id)
            return self._record(session, row) if row else None

    def get_for_organization(self, event_id: str, organization_id: str) -> EventRecord | None:
        with self.factory() as session:
            row = session.scalar(select(EventModel).where(EventModel.id == event_id, EventModel.organization_id == organization_id))
            return self._record(session, row) if row else None

    def get_by_movement_key(self, idempotency_key: str, organization_id: str) -> EventRecord | None:
        with self.factory() as session:
            movement = session.scalar(select(StockMovementModel).where(StockMovementModel.organization_id == organization_id, StockMovementModel.idempotency_key == idempotency_key))
            row = session.get(EventModel, movement.event_id) if movement else None
            return self._record(session, row) if row else None

    def list_events(self, organization_id: str | None = None) -> list[EventRecord]:
        with self.factory() as session:
            statement = select(EventModel)
            if organization_id:
                statement = statement.where(EventModel.organization_id == organization_id)
            rows = session.scalars(statement.order_by(EventModel.starts_at, EventModel.title, EventModel.id))
            return [self._record(session, row) for row in rows]

    def suggest_from_history(self, description: str, organization_id: str | None = None) -> list[Requirement]:
        tokens = self._tokens(description)
        suggestions: dict[str, int] = {}
        for event in self.list_events(organization_id):
            if len(tokens.intersection(self._tokens(event.description))) < 2:
                continue
            for requirement in event.requested_items:
                suggestions[requirement.item_id] = max(suggestions.get(requirement.item_id, 0), requirement.amount)
        return [Requirement(item_id=item_id, amount=amount) for item_id, amount in sorted(suggestions.items())]

    def active_reservations(self, exclude_event_id: str | None = None, organization_id: str | None = None, start_date: str | None = None, start_time: str = "00:00", duration_minutes: int = 1440) -> dict[str, int]:
        reservations: dict[str, int] = {}
        for event in self.list_events(organization_id):
            # Confirmed and packed quantities are already absent from the available bucket.
            if event.id == exclude_event_id or event.status != "planning":
                continue
            if start_date and not self._events_overlap(event, start_date, start_time, duration_minutes):
                continue
            for line in event.plan.get("lines", []):
                if int(line.get("missing", 0)) > 0:
                    continue
                item_id = str(line.get("item_id", ""))
                if item_id:
                    reservations[item_id] = reservations.get(item_id, 0) + int(line.get("amount", 1))
        return reservations

    def overlapping_events(self, start_date: str, start_time: str, duration_minutes: int, organization_id: str, exclude_event_id: str | None = None) -> list[EventRecord]:
        return [event for event in self.list_events(organization_id) if event.id != exclude_event_id and event.status in {"planning", "confirmed", "packed", "out"} and self._events_overlap(event, start_date, start_time, duration_minutes)]

    def to_dict(self, organization_id: str | None = None) -> dict[str, Any]:
        events = self.list_events(organization_id)
        return {"events": [event.to_dict() for event in events], "learning_count": len(events), "active_reservations": self.active_reservations(organization_id=organization_id)}

    def _record(self, session: Session, row: EventModel) -> EventRecord:
        data = dict(row.data or {})
        data.update({"id": row.id, "organization_id": row.organization_id, "owner_id": row.owner_user_id, "title": row.title, "status": row.status})
        movements = session.scalars(select(StockMovementModel).where(StockMovementModel.organization_id == row.organization_id, StockMovementModel.event_id == row.id).order_by(StockMovementModel.created_at, StockMovementModel.id))
        data["movements"] = [
            {
                "id": movement.id,
                "idempotency_key": movement.idempotency_key,
                "organization_id": movement.organization_id,
                "event_id": movement.event_id,
                "action": "dispatch" if movement.action == "out" else "return" if movement.action == "returned" else movement.action,
                "actor_id": movement.actor_membership_id,
                "lines": movement.lines,
                "created_at": _aware(movement.created_at).isoformat(),
            }
            for movement in movements
        ]
        return EventRecord.from_dict(data)

    @staticmethod
    def _tokens(description: str) -> set[str]:
        return {token.strip(".,:;!?()[]").lower() for token in description.split() if len(token.strip(".,:;!?()[]")) > 2}

    @staticmethod
    def _events_overlap(event: EventRecord, start_date: str, start_time: str, duration_minutes: int) -> bool:
        try:
            requested_start = datetime.fromisoformat(f"{start_date}T{start_time}:00")
            requested_end = requested_start + timedelta(minutes=duration_minutes)
            event_start = datetime.fromisoformat(f"{event.start_date}T{event.start_time}:00")
            return requested_start < event_start + timedelta(minutes=event.duration_minutes) and event_start < requested_end
        except ValueError:
            return False


class PostgresItemClassCatalog(ItemClassCatalog):
    def __init__(self, factory: sessionmaker[Session]):
        super().__init__(None)
        self.factory = factory

    def get(self, class_id: str, organization_id: str | None = None) -> ConfiguredItemClass | None:
        normalized = normalize_item_id(class_id)
        if organization_id:
            with self.factory() as session:
                row = session.scalar(select(ItemClassRecordModel).where(ItemClassRecordModel.organization_id == organization_id, ItemClassRecordModel.class_id == normalized))
                if row:
                    return ConfiguredItemClass.from_dict(row.data)
        return self.system_classes.get(normalized)

    def list_classes(self, organization_id: str | None = None) -> list[ConfiguredItemClass]:
        classes = dict(self.system_classes)
        if organization_id:
            with self.factory() as session:
                for row in session.scalars(select(ItemClassRecordModel).where(ItemClassRecordModel.organization_id == organization_id)):
                    item_class = ConfiguredItemClass.from_dict(row.data)
                    classes[item_class.id] = item_class
        return sorted(classes.values(), key=lambda item_class: (item_class.family, item_class.name))

    def upsert(self, item_class: ConfiguredItemClass, organization_id: str) -> ConfiguredItemClass:
        stored = ConfiguredItemClass.from_dict({**item_class.to_dict(), "organization_id": organization_id, "is_system": False})
        with self.factory.begin() as session:
            row = session.scalar(select(ItemClassRecordModel).where(ItemClassRecordModel.organization_id == organization_id, ItemClassRecordModel.class_id == stored.id).with_for_update())
            if row is None:
                row = ItemClassRecordModel(organization_id=organization_id, class_id=stored.id, data=stored.to_dict())
                session.add(row)
            else:
                row.data = stored.to_dict()
                row.version += 1
        return stored

    def remove(self, class_id: str, organization_id: str) -> bool:
        with self.factory.begin() as session:
            result = session.execute(delete(ItemClassRecordModel).where(ItemClassRecordModel.organization_id == organization_id, ItemClassRecordModel.class_id == normalize_item_id(class_id)))
            return bool(result.rowcount)


class PostgresIntegrationStore:
    def __init__(self, factory: sessionmaker[Session]):
        self.factory = factory

    def get_all(self, organization_id: str) -> dict[str, dict[str, Any]]:
        result = deepcopy(DEFAULT_INTEGRATIONS)
        with self.factory() as session:
            for row in session.scalars(select(IntegrationConnectionModel).where(IntegrationConnectionModel.organization_id == organization_id)):
                if row.integration_id in result:
                    result[row.integration_id].update(dict(row.data or {}))
        for integration in result.values():
            credential_env = integration.get("credential_env", "")
            integration["credential_present"] = bool(credential_env and os.environ.get(credential_env))
        return result

    def configure(self, organization_id: str, integration_id: str, values: dict[str, Any]) -> dict[str, Any]:
        if integration_id not in DEFAULT_INTEGRATIONS or integration_id == "excel":
            raise ValueError("Integration was not found or does not require configuration.")
        allowed = {"crm": {"provider", "endpoint"}, "google_sheets": {"spreadsheet_id", "sheet_name"}}[integration_id]
        cleaned = {name: str(values.get(name, "")).strip() for name in allowed}
        if integration_id == "crm":
            if not cleaned["endpoint"] or cleaned["provider"] not in {"generic", "hubspot", "salesforce"}:
                raise ValueError("CRM configuration is invalid.")
            IntegrationStore._validate_https_endpoint(self, cleaned["endpoint"])
        elif not cleaned["spreadsheet_id"]:
            raise ValueError("Google spreadsheet ID is required.")
        cleaned["credential_env"] = DEFAULT_INTEGRATIONS[integration_id]["credential_env"]
        cleaned["status"] = "ready" if os.environ.get(cleaned["credential_env"]) else "needs_credentials"
        cleaned["last_checked"] = utc_now().isoformat()
        self._store(organization_id, integration_id, cleaned)
        return self.get_all(organization_id)[integration_id]

    def record_transfer(self, organization_id: str, action: str) -> dict[str, Any]:
        data = {"status": "ready", "last_sync": utc_now().isoformat(), "last_action": action}
        self._store(organization_id, "excel", data)
        return self.get_all(organization_id)["excel"]

    def _store(self, organization_id: str, integration_id: str, data: dict[str, Any]) -> None:
        with self.factory.begin() as session:
            row = session.scalar(select(IntegrationConnectionModel).where(IntegrationConnectionModel.organization_id == organization_id, IntegrationConnectionModel.integration_id == integration_id).with_for_update())
            if row is None:
                session.add(IntegrationConnectionModel(organization_id=organization_id, integration_id=integration_id, data=data))
            else:
                row.data = data
                row.version += 1


class PostgresRuntime:
    def __init__(self, factory: sessionmaker[Session]):
        self.factory = factory
        self.accounts = PostgresAccountStore(factory)
        self.workspace = PostgresInventoryWorkspace(factory)
        self.memory = PostgresEventMemory(factory)
        self.item_classes = PostgresItemClassCatalog(factory)
        self.integrations = PostgresIntegrationStore(factory)
        self.operations = TransactionalEventOperations(factory)
        self.event_details = TransactionalEventDetails(factory)
        self.kit_operations = TransactionalKitOperations(factory)

    def create_session(self, user: UserAccount, max_age: int = 43_200) -> str:
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        membership_id = self.accounts.membership_id(user)
        with self.factory.begin() as session:
            session.add(
                SessionModel(
                    user_id=user.id,
                    organization_id=user.organization_id,
                    membership_id=membership_id,
                    token_hash=token_hash,
                    expires_at=utc_now() + timedelta(seconds=max_age),
                )
            )
        return token

    def register_workspace(
        self,
        *,
        name: str,
        email: str,
        password: str,
        organization_name: str,
    ) -> UserAccount:
        return self.accounts.register_workspace(
            name=name,
            email=email,
            password=password,
            organization_name=organization_name,
        )

    def current_user(self, token: str) -> UserAccount | None:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self.factory() as session:
            stored = session.scalar(select(SessionModel).where(SessionModel.token_hash == token_hash, SessionModel.revoked_at.is_(None)))
            if stored is None or _aware(stored.expires_at) <= utc_now():
                return None
            return self.accounts.get_for_membership(
                stored.membership_id,
                stored.organization_id,
                stored.user_id,
            )

    def revoke_session(self, token: str) -> None:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self.factory.begin() as session:
            stored = session.scalar(select(SessionModel).where(SessionModel.token_hash == token_hash).with_for_update())
            if stored and stored.revoked_at is None:
                stored.revoked_at = utc_now()

    def active_user_ids(self, organization_id: str) -> set[str]:
        with self.factory() as session:
            return set(
                session.scalars(
                    select(SessionModel.user_id)
                    .join(UserModel, UserModel.id == SessionModel.user_id)
                    .join(
                        MembershipModel,
                        and_(
                            MembershipModel.id == SessionModel.membership_id,
                            MembershipModel.user_id == SessionModel.user_id,
                            MembershipModel.organization_id
                            == SessionModel.organization_id,
                        ),
                    )
                    .where(
                        SessionModel.organization_id == organization_id,
                        SessionModel.revoked_at.is_(None),
                        SessionModel.expires_at > utc_now(),
                        UserModel.status == "active",
                        MembershipModel.status == "active",
                        MembershipModel.organization_id == organization_id,
                    )
                )
            )

    def transition(self, event_id: str, next_status: str, user: UserAccount, request_id: str, idempotency_key: str | None = None) -> EventRecord:
        self.operations.transition(
            user.organization_id,
            event_id,
            next_status,
            self.accounts.membership_id(user),
            request_id,
            idempotency_key,
        )
        event = self.memory.get_for_organization(event_id, user.organization_id)
        if event is None:
            raise ResourceNotFound("Event was not found.")
        return event

    def update_checklist(
        self,
        event_id: str,
        phase: str,
        item_id: str,
        done: bool,
        user: UserAccount,
        request_id: str,
    ) -> EventRecord:
        self.event_details.update_checklist(
            user.organization_id,
            event_id,
            phase,
            item_id,
            done,
            user.id,
            request_id,
        )
        event = self.memory.get_for_organization(event_id, user.organization_id)
        if event is None:
            raise ResourceNotFound("Event was not found.")
        return event

    def update_planning_plan(
        self,
        event: EventRecord,
        user: UserAccount,
        request_id: str,
        note: str,
    ) -> EventRecord:
        self.event_details.update_planning_plan(
            user.organization_id,
            event.id,
            event.plan,
            event.checklist,
            event.return_checklist,
            user.id,
            request_id,
            note,
        )
        stored = self.memory.get_for_organization(event.id, user.organization_id)
        if stored is None:
            raise ResourceNotFound("Event was not found.")
        return stored

    def checkout_kit(
        self,
        source_id: str,
        idempotency_key: str,
        user: UserAccount,
        request_id: str,
        event_data_factory: Callable[[], dict[str, Any]],
    ) -> tuple[EventRecord, bool]:
        event_row, created = self.kit_operations.checkout(
            user.organization_id,
            user.id,
            self.accounts.membership_id(user),
            source_id,
            idempotency_key,
            request_id,
            event_data_factory,
        )
        event = self.memory.get_for_organization(
            event_row.id, user.organization_id
        )
        if event is None:
            raise ResourceNotFound("Kit checkout event was not found.")
        return event, created
