from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

try:
    from tests.acceptance_support import postgres_required, race_required
except ModuleNotFoundError:
    from acceptance_support import postgres_required, race_required


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from database import (
    AllocationModel,
    AuditEventModel,
    Base,
    EventModel,
    InventoryAdjustmentModel,
    InventoryHoldingModel,
    MembershipModel,
    OperationRequestModel,
    OrganizationModel,
    StockMovementModel,
    TransactionalEventCreation,
    TransactionalEventDetails,
    TransactionalEventOperations,
    TransactionalInventoryOperations,
    TransactionalKitOperations,
    UserModel,
)
from accounts import UserAccount
from migrate_json_to_postgres import migrate_json
from postgres_runtime import PostgresRuntime
from security import AccessDenied, ResourceNotFound, StateConflict


def sqlite_factory() -> tuple[Session, sessionmaker[Session]]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


class TestTransactionalDatabase(unittest.TestCase):
    def setUp(self):
        self.engine, self.factory = sqlite_factory()
        with self.factory.begin() as session:
            for organization_id in ("org-a", "org-b"):
                session.add(OrganizationModel(id=organization_id, name=organization_id))
                user = UserModel(
                    id=f"owner-{organization_id}",
                    email=f"owner@{organization_id}.test",
                    name=f"Owner {organization_id}",
                    password_hash="test-only",
                )
                session.add(user)
                session.flush()
                session.add(
                    MembershipModel(
                        id=f"membership-{organization_id}",
                        organization_id=organization_id,
                        user_id=user.id,
                        role="owner",
                    )
                )
            session.flush()
            session.add_all(
                [
                    InventoryHoldingModel(
                        id="holding-a",
                        organization_id="org-a",
                        legacy_item_id="sm58",
                        scope="shared",
                        available_quantity=1,
                    ),
                    InventoryHoldingModel(
                        id="holding-b",
                        organization_id="org-b",
                        legacy_item_id="sm58",
                        scope="shared",
                        available_quantity=5,
                    ),
                    EventModel(
                        id="event-a",
                        organization_id="org-a",
                        owner_user_id="owner-org-a",
                        title="Organization A event",
                        status="planning",
                        data={
                            "plan_verified": True,
                            "plan": {
                                "lines": [
                                    {"item_id": "sm58", "amount": 1, "missing": 0}
                                ]
                            }
                        },
                    ),
                    EventModel(
                        id="event-b",
                        organization_id="org-b",
                        owner_user_id="owner-org-b",
                        title="Organization B event",
                        status="planning",
                        data={"plan": {"lines": []}},
                    ),
                ]
            )

    def tearDown(self):
        self.engine.dispose()

    def transition(self, status: str):
        return TransactionalEventOperations(self.factory).transition(
            "org-a",
            "event-a",
            status,
            "membership-org-a",
            f"request-{status}",
        )

    def test_event_creation_is_idempotent_and_payload_bound(self):
        service = TransactionalEventCreation(self.factory)
        payload = {
            "description": "Small event on 2026-09-10.",
            "overrides": {"title": "Retry-safe event"},
        }
        event_data = {
            "title": "Retry-safe event",
            "description": payload["description"],
            "start_date": "2026-09-10",
            "start_time": "18:00",
            "duration_minutes": 120,
            "plan": {"lines": []},
            "checklist": [],
            "return_checklist": [],
            "history": [],
        }

        first, created = service.create(
            "org-a",
            "owner-org-a",
            "event-create-key",
            "request-one",
            payload,
            event_data,
        )
        retried, retry_created = service.create(
            "org-a",
            "owner-org-a",
            "event-create-key",
            "request-two",
            payload,
            event_data,
        )
        with self.assertRaises(StateConflict):
            service.create(
                "org-a",
                "owner-org-a",
                "event-create-key",
                "request-three",
                {**payload, "description": "A different event."},
                event_data,
            )
        with self.factory() as session:
            events = session.scalar(
                select(func.count()).select_from(EventModel).where(
                    EventModel.organization_id == "org-a"
                )
            )
            operations = session.scalar(
                select(func.count()).select_from(OperationRequestModel).where(
                    OperationRequestModel.organization_id == "org-a",
                    OperationRequestModel.operation == "event.create",
                )
            )
            audits = session.scalar(
                select(func.count()).select_from(AuditEventModel).where(
                    AuditEventModel.organization_id == "org-a",
                    AuditEventModel.action == "event.created",
                )
            )
        self.assertTrue(created)
        self.assertFalse(retry_created)
        self.assertEqual(first.id, retried.id)
        self.assertEqual(events, 2)
        self.assertEqual(operations, 1)
        self.assertEqual(audits, 1)

    def test_event_creation_validates_and_persists_same_workspace_crew(self):
        with self.factory.begin() as session:
            session.add(
                UserModel(
                    id="operator-org-a",
                    email="operator@org-a.test",
                    name="Operator A",
                    password_hash="test-only",
                )
            )
            session.flush()
            session.add(
                MembershipModel(
                    id="membership-operator-org-a",
                    organization_id="org-a",
                    user_id="operator-org-a",
                    role="operator",
                )
            )
        data = {
            "title": "Crew event",
            "description": "Crew event on 2026-09-20.",
            "start_date": "2026-09-20",
            "start_time": "10:00",
            "duration_minutes": 120,
            "assigned_user_ids": ["operator-org-a"],
            "plan": {"lines": []},
        }
        row, _ = TransactionalEventCreation(self.factory).create(
            "org-a", "owner-org-a", "crew-event-key", "crew-request", {}, data
        )
        self.assertEqual(
            row.data["assigned_user_ids"], ["owner-org-a", "operator-org-a"]
        )
        with self.assertRaises(ValueError):
            TransactionalEventCreation(self.factory).create(
                "org-a",
                "owner-org-a",
                "foreign-crew-key",
                "foreign-crew-request",
                {},
                {**data, "assigned_user_ids": ["owner-org-b"]},
            )

    def test_cancel_releases_reservation_and_preserves_history(self):
        self.transition("confirmed")
        cancelled = TransactionalEventOperations(self.factory).cancel(
            "org-a", "event-a", "owner-org-a", "cancel-request"
        )
        retried = TransactionalEventOperations(self.factory).cancel(
            "org-a", "event-a", "owner-org-a", "cancel-retry"
        )
        with self.factory() as session:
            holding = session.get(InventoryHoldingModel, "holding-a")
            movement_count = session.scalar(
                select(func.count(StockMovementModel.id)).where(
                    StockMovementModel.organization_id == "org-a",
                    StockMovementModel.event_id == "event-a",
                    StockMovementModel.action == "cancelled",
                )
            )
            allocation_count = session.scalar(
                select(func.count(AllocationModel.id)).where(
                    AllocationModel.event_id == "event-a"
                )
            )
        self.assertEqual((cancelled.status, retried.status), ("cancelled", "cancelled"))
        self.assertEqual((holding.available_quantity, holding.reserved_quantity), (1, 0))
        self.assertEqual((movement_count, allocation_count), (1, 0))

    def test_only_empty_planning_event_can_be_deleted(self):
        service = TransactionalEventOperations(self.factory)
        service.delete_draft("org-a", "event-a", "owner-org-a", "delete-request")
        with self.factory() as session:
            self.assertIsNone(session.get(EventModel, "event-a"))
            self.assertEqual(
                session.scalar(
                    select(func.count(AuditEventModel.id)).where(
                        AuditEventModel.action == "event.deleted",
                        AuditEventModel.resource_id == "event-a",
                    )
                ),
                1,
            )

    def test_saved_kit_is_scoped_and_uses_visible_inventory(self):
        runtime = PostgresRuntime(self.factory)
        kit = runtime.kits.create(
            {
                "name": "Microphone package",
                "description": "A reusable package.",
                "items": [{"item_id": "sm58", "amount": 1}],
            },
            "org-a",
            "owner-org-a",
            "kit-create-request",
        )
        self.assertEqual(runtime.kits.get(kit.id, "org-a").name, "Microphone package")
        self.assertIsNone(runtime.kits.get(kit.id, "org-b"))
        self.assertEqual([stored.id for stored in runtime.kits.list_kits("org-a")], [kit.id])

    def test_event_edit_is_versioned_scoped_and_lifecycle_safe(self):
        details = TransactionalEventDetails(self.factory)
        edited_data = {
            "title": "Edited event",
            "description": "Edited brief",
            "start_date": "2026-09-12",
            "start_time": "19:00",
            "duration_minutes": 180,
            "location": "New venue",
            "attendee_count": 45,
            "priority_score": 60,
            "plan": {"lines": []},
            "checklist": [],
            "return_checklist": [],
            "history": [],
        }

        updated = details.update_event(
            "org-a",
            "event-a",
            1,
            edited_data,
            "owner-org-a",
            "edit-event-a",
        )
        with self.assertRaises(StateConflict):
            details.update_event(
                "org-a",
                "event-a",
                1,
                edited_data,
                "owner-org-a",
                "stale-edit-event-a",
            )
        with self.assertRaises(ResourceNotFound):
            details.update_event(
                "org-a",
                "event-b",
                1,
                edited_data,
                "owner-org-a",
                "cross-org-edit",
            )
        with self.factory.begin() as session:
            event = session.get(EventModel, "event-a")
            event.status = "confirmed"
        with self.assertRaises(StateConflict):
            details.update_event(
                "org-a",
                "event-a",
                2,
                edited_data,
                "owner-org-a",
                "restricted-edit-event-a",
            )

        self.assertEqual(updated.title, "Edited event")
        self.assertEqual(updated.version, 2)
        self.assertEqual(updated.data["attendee_count"], 45)
        self.assertEqual(updated.data["history"][-1]["action"], "edited")

    def test_full_transition_moves_each_quantity_exactly_once(self):
        self.transition("confirmed")
        self.transition("packed")
        self.transition("out")
        self.transition("out")
        self.transition("returned")
        self.transition("returned")

        with self.factory() as session:
            holding_a = session.get(InventoryHoldingModel, "holding-a")
            holding_b = session.get(InventoryHoldingModel, "holding-b")
            event_a = session.get(EventModel, "event-a")
            allocation = session.scalar(
                select(AllocationModel).where(AllocationModel.event_id == "event-a")
            )
            movements = session.scalar(
                select(func.count()).select_from(StockMovementModel).where(
                    StockMovementModel.organization_id == "org-a"
                )
            )

        self.assertEqual(event_a.status, "returned")
        self.assertEqual(allocation.status, "returned")
        self.assertEqual(
            (
                holding_a.available_quantity,
                holding_a.reserved_quantity,
                holding_a.packed_quantity,
                holding_a.dispatched_quantity,
            ),
            (1, 0, 0, 0),
        )
        self.assertEqual(holding_b.available_quantity, 5)
        self.assertEqual(movements, 4)

    def test_invalid_transition_rolls_back_without_stock_change(self):
        with self.assertRaises(StateConflict):
            self.transition("out")

        with self.factory() as session:
            event_a = session.get(EventModel, "event-a")
            holding_a = session.get(InventoryHoldingModel, "holding-a")

        self.assertEqual(event_a.status, "planning")
        self.assertEqual(holding_a.available_quantity, 1)

    def test_tenant_scope_hides_foreign_event(self):
        with self.assertRaises(ResourceNotFound):
            TransactionalEventOperations(self.factory).transition(
                "org-a",
                "event-b",
                "confirmed",
                "membership-org-a",
                "request-cross-tenant",
            )

    def test_database_rejects_cross_tenant_event_owner(self):
        with self.assertRaises(IntegrityError):
            with self.factory.begin() as session:
                session.add(
                    EventModel(
                        id="invalid-owner",
                        organization_id="org-a",
                        owner_user_id="owner-org-b",
                        title="Invalid",
                        status="planning",
                    )
                )

    def test_database_rejects_negative_inventory(self):
        with self.assertRaises(IntegrityError):
            with self.factory.begin() as session:
                session.add(
                    InventoryHoldingModel(
                        organization_id="org-a",
                        legacy_item_id="invalid",
                        scope="shared",
                        available_quantity=-1,
                    )
                )

    def test_reservation_rejects_another_users_personal_inventory(self):
        with self.factory.begin() as session:
            other_user = UserModel(
                id="other-tech",
                email="other-tech@org-a.test",
                name="Other Technician",
                password_hash="test-only",
            )
            session.add(other_user)
            session.flush()
            session.add(
                MembershipModel(
                    id="membership-other-tech",
                    organization_id="org-a",
                    user_id=other_user.id,
                    role="technician",
                )
            )
            session.flush()
            session.add_all(
                [
                    InventoryHoldingModel(
                        id="other-private-mic",
                        organization_id="org-a",
                        legacy_item_id="private mic",
                        scope="personal",
                        owner_user_id=other_user.id,
                        available_quantity=1,
                    ),
                    EventModel(
                        id="private-inventory-event",
                        organization_id="org-a",
                        owner_user_id="owner-org-a",
                        title="Private inventory boundary",
                        status="planning",
                        data={
                            "plan_verified": True,
                            "plan": {
                                "lines": [
                                    {
                                        "item_id": "private mic",
                                        "amount": 1,
                                        "missing": 0,
                                    }
                                ]
                            },
                        },
                    ),
                ]
            )

        with self.assertRaises(StateConflict):
            TransactionalEventOperations(self.factory).transition(
                "org-a",
                "private-inventory-event",
                "confirmed",
                "membership-org-a",
                "private-boundary-request",
            )

        with self.factory() as session:
            event_row = session.get(EventModel, "private-inventory-event")
            holding = session.get(InventoryHoldingModel, "other-private-mic")
            allocation_count = session.scalar(
                select(func.count()).select_from(AllocationModel).where(
                    AllocationModel.event_id == "private-inventory-event"
                )
            )
        self.assertEqual(event_row.status, "planning")
        self.assertEqual((holding.available_quantity, holding.reserved_quantity), (1, 0))
        self.assertEqual(allocation_count, 0)

    def test_reservation_can_use_event_owners_personal_inventory(self):
        with self.factory.begin() as session:
            session.add_all(
                [
                    InventoryHoldingModel(
                        id="owner-private-mic",
                        organization_id="org-a",
                        legacy_item_id="owner mic",
                        scope="personal",
                        owner_user_id="owner-org-a",
                        available_quantity=1,
                    ),
                    EventModel(
                        id="owner-personal-event",
                        organization_id="org-a",
                        owner_user_id="owner-org-a",
                        title="Owner personal inventory",
                        status="planning",
                        data={
                            "plan_verified": True,
                            "plan": {
                                "lines": [
                                    {"item_id": "owner mic", "amount": 1, "missing": 0}
                                ]
                            },
                        },
                    ),
                ]
            )

        TransactionalEventOperations(self.factory).transition(
            "org-a",
            "owner-personal-event",
            "confirmed",
            "membership-org-a",
            "owner-personal-request",
        )

        with self.factory() as session:
            event_row = session.get(EventModel, "owner-personal-event")
            holding = session.get(InventoryHoldingModel, "owner-private-mic")
        self.assertEqual(event_row.status, "confirmed")
        self.assertEqual((holding.available_quantity, holding.reserved_quantity), (0, 1))

    def test_atomic_kit_checkout_is_idempotent_and_rejects_conflicting_source(self):
        service = TransactionalKitOperations(self.factory)

        def event_data(source_id: str = "test-kit"):
            return {
                "title": "Test kit checkout",
                "description": "Atomic checkout test",
                "start_date": "2026-09-10",
                "source_type": "kit",
                "source_id": source_id,
                "plan": {
                    "lines": [{"item_id": "sm58", "amount": 1, "missing": 0}]
                },
                "checklist": [
                    {"item_id": "sm58", "amount": 1, "done": False}
                ],
                "return_checklist": [],
                "conflicts": [],
                "history": [],
            }

        first, first_created = service.checkout(
            "org-a",
            "owner-org-a",
            "membership-org-a",
            "test-kit",
            "atomic-kit-key",
            "atomic-kit-request",
            event_data,
        )
        second, second_created = service.checkout(
            "org-a",
            "owner-org-a",
            "membership-org-a",
            "test-kit",
            "atomic-kit-key",
            "atomic-kit-retry",
            event_data,
        )
        with self.assertRaises(StateConflict):
            service.checkout(
                "org-a",
                "owner-org-a",
                "membership-org-a",
                "different-kit",
                "atomic-kit-key",
                "atomic-kit-conflict",
                lambda: event_data("different-kit"),
            )
        other_organization, other_created = service.checkout(
            "org-b",
            "owner-org-b",
            "membership-org-b",
            "test-kit",
            "atomic-kit-key",
            "atomic-kit-other-org",
            event_data,
        )

        with self.factory() as session:
            holding = session.get(InventoryHoldingModel, "holding-a")
            operation_count = session.scalar(
                select(func.count()).select_from(OperationRequestModel).where(
                    OperationRequestModel.organization_id == "org-a",
                    OperationRequestModel.idempotency_key == "atomic-kit-key",
                )
            )
            movement_count = session.scalar(
                select(func.count()).select_from(StockMovementModel).where(
                    StockMovementModel.event_id == first.id
                )
            )
            audit_count = session.scalar(
                select(func.count()).select_from(AuditEventModel).where(
                    AuditEventModel.resource_id == first.id
                )
            )
            allocation = session.scalar(
                select(AllocationModel).where(AllocationModel.event_id == first.id)
            )

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertTrue(other_created)
        self.assertEqual(first.id, second.id)
        self.assertNotEqual(first.id, other_organization.id)
        self.assertEqual(other_organization.organization_id, "org-b")
        self.assertEqual(first.status, "out")
        self.assertEqual((holding.available_quantity, holding.dispatched_quantity), (0, 1))
        self.assertEqual(operation_count, 1)
        self.assertEqual(movement_count, 3)
        self.assertEqual(audit_count, 4)
        self.assertEqual(allocation.status, "dispatched")

    def test_atomic_kit_checkout_failure_rolls_back_every_row(self):
        service = TransactionalKitOperations(self.factory)

        with self.factory() as session:
            before = {
                model.__tablename__: session.scalar(
                    select(func.count()).select_from(model)
                )
                for model in (
                    EventModel,
                    AllocationModel,
                    StockMovementModel,
                    AuditEventModel,
                    OperationRequestModel,
                )
            }

        with self.assertRaises(StateConflict):
            service.checkout(
                "org-a",
                "owner-org-a",
                "membership-org-a",
                "missing-kit",
                "rollback-kit-key",
                "rollback-kit-request",
                lambda: {
                    "title": "Missing kit checkout",
                    "source_type": "kit",
                    "source_id": "missing-kit",
                    "plan": {
                        "lines": [
                            {"item_id": "not stocked", "amount": 1, "missing": 0}
                        ]
                    },
                    "checklist": [],
                    "return_checklist": [],
                    "conflicts": [],
                    "history": [],
                },
            )

        with self.factory() as session:
            after = {
                model.__tablename__: session.scalar(
                    select(func.count()).select_from(model)
                )
                for model in (
                    EventModel,
                    AllocationModel,
                    StockMovementModel,
                    AuditEventModel,
                    OperationRequestModel,
                )
            }
            holding = session.get(InventoryHoldingModel, "holding-a")
        self.assertEqual(after, before)
        self.assertEqual((holding.available_quantity, holding.dispatched_quantity), (1, 0))

    def test_existing_session_rejects_disabled_user_and_membership(self):
        runtime = PostgresRuntime(self.factory)
        user = UserAccount(
            id="owner-org-a",
            name="Owner org-a",
            email="owner@org-a.test",
            role="owner",
            organization_id="org-a",
            organization_name="org-a",
        )
        token = runtime.create_session(user)
        self.assertIsNotNone(runtime.current_user(token))

        with self.factory.begin() as session:
            session.get(UserModel, "owner-org-a").status = "disabled"
        self.assertIsNone(runtime.current_user(token))

        with self.factory.begin() as session:
            session.get(UserModel, "owner-org-a").status = "active"
            session.get(MembershipModel, "membership-org-a").status = "disabled"
        self.assertIsNone(runtime.current_user(token))

    def test_event_detail_commands_cannot_restore_stale_operational_state(self):
        with self.factory.begin() as session:
            event_row = session.get(EventModel, "event-a")
            event_row.data = {
                **dict(event_row.data or {}),
                "checklist": [{"item_id": "sm58", "amount": 1, "done": True}],
                "return_checklist": [
                    {"item_id": "sm58", "amount": 1, "done": True}
                ],
                "history": [],
            }
        for status in ("confirmed", "packed", "out"):
            self.transition(status)

        runtime = PostgresRuntime(self.factory)
        stale = runtime.memory.get_for_organization("event-a", "org-a")
        self.transition("returned")
        stale.checklist[0]["done"] = False
        with self.assertRaises(StateConflict):
            runtime.memory.add(stale)

        details = TransactionalEventDetails(self.factory)
        details.update_checklist(
            "org-a",
            "event-a",
            "pack",
            "sm58",
            False,
            "owner-org-a",
            "detail-after-return",
        )
        with self.factory() as session:
            event_row = session.get(EventModel, "event-a")
            allocation = session.scalar(
                select(AllocationModel).where(AllocationModel.event_id == "event-a")
            )
            holding = session.get(InventoryHoldingModel, "holding-a")
            return_movements = session.scalar(
                select(func.count()).select_from(StockMovementModel).where(
                    StockMovementModel.event_id == "event-a",
                    StockMovementModel.action == "returned",
                )
            )
            checklist_audits = session.scalar(
                select(func.count()).select_from(AuditEventModel).where(
                    AuditEventModel.resource_id == "event-a",
                    AuditEventModel.action == "event.checklist",
                )
            )
        self.assertEqual(event_row.status, "returned")
        self.assertEqual(allocation.status, "returned")
        self.assertEqual((holding.available_quantity, holding.dispatched_quantity), (1, 0))
        self.assertEqual(return_movements, 1)
        self.assertFalse(event_row.data["checklist"][0]["done"])
        self.assertEqual(checklist_audits, 1)

    def test_inventory_adjustments_are_ledgered_idempotent_and_archived(self):
        service = TransactionalInventoryOperations(self.factory)
        added, created = service.adjust(
            "org-a",
            "owner-org-a",
            "shared",
            None,
            "sm58",
            2,
            {"type": "microphone"},
            "inventory.add",
            "add-sm58",
            "add-sm58",
            "stock_received",
            "Two microphones received.",
            "test",
        )
        retried, retry_created = service.adjust(
            "org-a",
            "owner-org-a",
            "shared",
            None,
            "sm58",
            2,
            {"type": "microphone"},
            "inventory.add",
            "add-sm58",
            "retry-add-sm58",
            "stock_received",
            "Two microphones received.",
            "test",
        )
        with self.assertRaises(StateConflict):
            service.adjust(
                "org-a",
                "owner-org-a",
                "shared",
                None,
                "sm58",
                1,
                {},
                "inventory.add",
                "add-sm58",
                "conflicting-add",
                "stock_received",
                "Different payload.",
                "test",
            )
        service.adjust(
            "org-a",
            "owner-org-a",
            "shared",
            None,
            "sm58",
            3,
            {},
            "inventory.remove",
            "remove-sm58",
            "remove-sm58",
            "stock_removed",
            "Retired from service.",
            "test",
        )

        with self.factory() as session:
            holding = session.get(InventoryHoldingModel, "holding-a")
            adjustments = list(
                session.scalars(
                    select(InventoryAdjustmentModel)
                    .where(InventoryAdjustmentModel.holding_id == "holding-a")
                    .order_by(InventoryAdjustmentModel.created_at)
                )
            )
            archived_audit = session.scalar(
                select(func.count()).select_from(AuditEventModel).where(
                    AuditEventModel.resource_id == "holding-a",
                    AuditEventModel.action == "inventory.definition_archived",
                )
            )
        self.assertTrue(created)
        self.assertFalse(retry_created)
        self.assertEqual(added.id, retried.id)
        self.assertFalse(holding.active)
        self.assertEqual(holding.available_quantity, 0)
        self.assertEqual(
            [(row.before_quantity, row.after_quantity, row.delta) for row in adjustments],
            [(1, 3, 2), (3, 0, -3)],
        )
        self.assertEqual(archived_audit, 1)

        reactivated, _ = service.adjust(
            "org-a",
            "owner-org-a",
            "shared",
            None,
            "sm58",
            1,
            {},
            "inventory.add",
            "reactivate-sm58",
            "reactivate-sm58",
            "stock_received",
            "Returned to service.",
            "test",
        )
        self.assertEqual(reactivated.id, "holding-a")
        self.assertTrue(reactivated.active)

    def test_inventory_import_reconciliation_ledger_and_permissions(self):
        service = TransactionalInventoryOperations(self.factory)
        unchanged = [
            {"item_id": "sm58", "quantity": 1, "metadata": {}}
        ]
        service.reconcile(
            "org-a",
            "owner-org-a",
            "shared",
            None,
            unchanged,
            "import-unchanged",
            "import-unchanged",
            "csv_reconciliation",
            "CSV inventory reconciliation.",
            "csv_import",
        )
        with self.factory() as session:
            self.assertEqual(
                session.scalar(select(func.count()).select_from(InventoryAdjustmentModel)),
                0,
            )
            self.assertEqual(
                session.scalar(
                    select(func.count()).select_from(AuditEventModel).where(
                        AuditEventModel.action == "inventory.adjusted"
                    )
                ),
                0,
            )

        changed = [{"item_id": "sm58", "quantity": 4, "metadata": {}}]
        service.reconcile(
            "org-a",
            "owner-org-a",
            "shared",
            None,
            changed,
            "import-increase",
            "import-increase",
            "csv_reconciliation",
            "CSV inventory reconciliation.",
            "csv_import",
        )
        service.reconcile(
            "org-a",
            "owner-org-a",
            "shared",
            None,
            [{"item_id": "sm58", "quantity": 2, "metadata": {}}],
            "import-decrease",
            "import-decrease",
            "csv_reconciliation",
            "CSV inventory reconciliation.",
            "csv_import",
        )
        service.reconcile(
            "org-a",
            "owner-org-a",
            "shared",
            None,
            [{"item_id": "sm58", "quantity": 2, "metadata": {}}],
            "import-decrease",
            "import-decrease-retry",
            "csv_reconciliation",
            "CSV inventory reconciliation.",
            "csv_import",
        )
        with self.assertRaises(ResourceNotFound):
            service.adjust(
                "org-b",
                "owner-org-a",
                "shared",
                None,
                "sm58",
                1,
                {},
                "inventory.add",
                "cross-org",
                "cross-org",
                "stock_received",
                "Cross organization attempt.",
                "test",
            )

        with self.factory.begin() as session:
            session.add(
                UserModel(
                    id="client-org-a",
                    email="client@org-a.test",
                    name="Client",
                    password_hash="test-only",
                )
            )
            session.flush()
            session.add(
                MembershipModel(
                    id="client-membership-org-a",
                    organization_id="org-a",
                    user_id="client-org-a",
                    role="client",
                )
            )
        with self.assertRaises(AccessDenied):
            service.reconcile(
                "org-a",
                "client-org-a",
                "shared",
                None,
                changed,
                "client-import",
                "client-import",
                "csv_reconciliation",
                "Unauthorized reconciliation.",
                "csv_import",
            )
        with self.factory() as session:
            holding = session.get(InventoryHoldingModel, "holding-a")
            adjustments = list(
                session.scalars(
                    select(InventoryAdjustmentModel)
                    .where(InventoryAdjustmentModel.holding_id == "holding-a")
                    .order_by(InventoryAdjustmentModel.created_at)
                )
            )
        self.assertEqual(holding.available_quantity, 2)
        self.assertEqual(
            [(row.before_quantity, row.after_quantity, row.delta) for row in adjustments],
            [(1, 4, 3), (4, 2, -2)],
        )

    def test_inventory_adjustment_failure_rolls_back_quantity_and_ledger(self):
        class FailingInventoryOperations(TransactionalInventoryOperations):
            def _record_adjustment(self, *args, **kwargs):
                super()._record_adjustment(*args, **kwargs)
                raise RuntimeError("forced audit failure")

        with self.assertRaisesRegex(RuntimeError, "forced audit failure"):
            FailingInventoryOperations(self.factory).adjust(
                "org-a",
                "owner-org-a",
                "shared",
                None,
                "sm58",
                2,
                {},
                "inventory.add",
                "rollback-adjustment",
                "rollback-adjustment",
                "stock_received",
                "Rollback proof.",
                "test",
            )
        with self.factory() as session:
            holding = session.get(InventoryHoldingModel, "holding-a")
            adjustment_count = session.scalar(
                select(func.count()).select_from(InventoryAdjustmentModel)
            )
            audit_count = session.scalar(
                select(func.count()).select_from(AuditEventModel)
            )
            request_count = session.scalar(
                select(func.count()).select_from(OperationRequestModel).where(
                    OperationRequestModel.idempotency_key == "rollback-adjustment"
                )
            )
        self.assertEqual(holding.available_quantity, 1)
        self.assertEqual((adjustment_count, audit_count, request_count), (0, 0, 0))

    @postgres_required
    @race_required
    def test_postgres_concurrent_reservation(self):
        database_url = os.environ["SALAMANDRA_TEST_POSTGRES_URL"]
        schema = f"salamandra_test_{uuid4().hex}"
        admin_engine = create_engine(database_url, isolation_level="AUTOCOMMIT")
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(
            database_url,
            connect_args={"options": f"-csearch_path={schema}"},
        )
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        try:
            Base.metadata.create_all(engine)
            with factory.begin() as session:
                session.add(OrganizationModel(id="org-race", name="Race organization"))
                session.add(
                    UserModel(
                        id="race-owner",
                        email="race-owner@example.test",
                        name="Race Owner",
                        password_hash="test-only",
                    )
                )
                session.flush()
                session.add(
                    MembershipModel(
                        id="race-membership",
                        organization_id="org-race",
                        user_id="race-owner",
                        role="owner",
                    )
                )
                session.flush()
                session.add(
                    InventoryHoldingModel(
                        id="race-holding",
                        organization_id="org-race",
                        legacy_item_id="scarce-pa",
                        scope="shared",
                        available_quantity=1,
                    )
                )
                for event_id in ("race-event-1", "race-event-2"):
                    session.add(
                        EventModel(
                            id=event_id,
                            organization_id="org-race",
                            owner_user_id="race-owner",
                            title=event_id,
                            status="planning",
                            data={
                                "plan_verified": True,
                                "plan": {
                                    "lines": [
                                        {
                                            "item_id": "scarce-pa",
                                            "amount": 1,
                                            "missing": 0,
                                        }
                                    ]
                                }
                            },
                        )
                    )

            barrier = threading.Barrier(2)
            results: list[str] = []

            def reserve(event_id: str):
                barrier.wait()
                try:
                    TransactionalEventOperations(factory).transition(
                        "org-race",
                        event_id,
                        "confirmed",
                        "race-membership",
                        f"request-{event_id}",
                    )
                    results.append("reserved")
                except StateConflict:
                    results.append("conflict")

            threads = [
                threading.Thread(target=reserve, args=(event_id,))
                for event_id in ("race-event-1", "race-event-2")
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)

            with factory() as session:
                holding = session.get(InventoryHoldingModel, "race-holding")
                allocation_count = session.scalar(
                    select(func.count()).select_from(AllocationModel)
                )

            self.assertCountEqual(results, ["reserved", "conflict"])
            self.assertEqual(
                (holding.available_quantity, holding.reserved_quantity),
                (0, 1),
            )
            self.assertEqual(allocation_count, 1)
        finally:
            engine.dispose()
            with admin_engine.connect() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin_engine.dispose()

    @postgres_required
    @race_required
    def test_postgres_concurrent_inventory_adjustments_are_serialized(self):
        database_url = os.environ["SALAMANDRA_TEST_POSTGRES_URL"]
        schema = f"salamandra_inventory_race_{uuid4().hex}"
        admin_engine = create_engine(database_url, isolation_level="AUTOCOMMIT")
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(
            database_url,
            connect_args={"options": f"-csearch_path={schema}"},
        )
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        try:
            Base.metadata.create_all(engine)
            with factory.begin() as session:
                session.add(OrganizationModel(id="inventory-race-org", name="Inventory race"))
                session.add(
                    UserModel(
                        id="inventory-race-owner",
                        email="inventory-race@example.test",
                        name="Inventory Race Owner",
                        password_hash="test-only",
                    )
                )
                session.flush()
                session.add(
                    MembershipModel(
                        id="inventory-race-membership",
                        organization_id="inventory-race-org",
                        user_id="inventory-race-owner",
                        role="owner",
                    )
                )
                session.flush()
                session.add(
                    InventoryHoldingModel(
                        id="inventory-race-holding",
                        organization_id="inventory-race-org",
                        legacy_item_id="race-case",
                        scope="shared",
                        available_quantity=0,
                    )
                )

            def add_stock(key: str, barrier: threading.Barrier, results: list[bool]):
                barrier.wait(timeout=10)
                _, created = TransactionalInventoryOperations(factory).adjust(
                    "inventory-race-org",
                    "inventory-race-owner",
                    "shared",
                    None,
                    "race-case",
                    1,
                    {},
                    "inventory.add",
                    key,
                    key,
                    "stock_received",
                    "Concurrent inventory receipt.",
                    "test",
                )
                results.append(created)

            duplicate_barrier = threading.Barrier(2)
            duplicate_results: list[bool] = []
            duplicate_threads = [
                threading.Thread(
                    target=add_stock,
                    args=("same-adjustment-key", duplicate_barrier, duplicate_results),
                )
                for _ in range(2)
            ]
            for thread in duplicate_threads:
                thread.start()
            for thread in duplicate_threads:
                thread.join(timeout=15)
            self.assertCountEqual(duplicate_results, [True, False])

            independent_barrier = threading.Barrier(2)
            independent_results: list[bool] = []
            independent_threads = [
                threading.Thread(
                    target=add_stock,
                    args=(key, independent_barrier, independent_results),
                )
                for key in ("adjustment-key-a", "adjustment-key-b")
            ]
            for thread in independent_threads:
                thread.start()
            for thread in independent_threads:
                thread.join(timeout=15)

            with factory() as session:
                holding = session.get(InventoryHoldingModel, "inventory-race-holding")
                adjustment_count = session.scalar(
                    select(func.count()).select_from(InventoryAdjustmentModel)
                )
                request_count = session.scalar(
                    select(func.count()).select_from(OperationRequestModel).where(
                        OperationRequestModel.organization_id == "inventory-race-org",
                        OperationRequestModel.operation == "inventory.add",
                    )
                )
            self.assertEqual(independent_results, [True, True])
            self.assertEqual(holding.available_quantity, 3)
            self.assertEqual((adjustment_count, request_count), (3, 3))
        finally:
            engine.dispose()
            with admin_engine.connect() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin_engine.dispose()


class TestJsonMigration(unittest.TestCase):
    def test_json_migration_preserves_tenant_ownership_and_counts(self):
        engine, factory = sqlite_factory()
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                docs = Path(temp_dir)
                (docs / "users.json").write_text(
                    json.dumps(
                        {
                            "users": [
                                {
                                    "id": "owner-a",
                                    "email": "owner-a@example.test",
                                    "name": "Owner A",
                                    "password_hash": "legacy",
                                    "role": "owner",
                                    "organization_id": "org-a",
                                    "organization_name": "Organization A",
                                }
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
                (docs / "inventories.json").write_text(
                    json.dumps(
                        {
                            "shared": {
                                "org-a": {
                                    "items": [
                                        {"id": "SM58", "count": 3, "in_use_count": 1}
                                    ]
                                }
                            },
                            "personal": {},
                        }
                    ),
                    encoding="utf-8",
                )
                (docs / "events.json").write_text(
                    json.dumps(
                        {
                            "events": [
                                {
                                    "id": "event-a",
                                    "organization_id": "org-a",
                                    "owner_id": "owner-a",
                                    "title": "Migrated event",
                                    "status": "planning",
                                    "start_date": "2026-09-10",
                                    "start_time": "10:00",
                                }
                            ]
                        }
                    ),
                    encoding="utf-8",
                )

                report = migrate_json(factory, docs)

            with factory() as session:
                holding = session.scalar(select(InventoryHoldingModel))
                event_row = session.get(EventModel, "event-a")
                audit_count = session.scalar(
                    select(func.count()).select_from(AuditEventModel)
                )

            self.assertEqual(report.to_dict()["organizations"], 1)
            self.assertEqual((holding.available_quantity, holding.dispatched_quantity), (3, 1))
            self.assertEqual(event_row.organization_id, "org-a")
            self.assertTrue(any("unattributed" in warning for warning in report.warnings))
            self.assertEqual(audit_count, 0)
        finally:
            engine.dispose()


class TestAlembicMigration(unittest.TestCase):
    def test_revision_identifiers_fit_alembic_version_column(self):
        versions = ROOT / "migrations" / "versions"
        for migration in versions.glob("*.py"):
            namespace: dict[str, object] = {}
            exec(compile(migration.read_text(encoding="utf-8"), migration, "exec"), namespace)
            with self.subTest(migration=migration.name):
                self.assertLessEqual(len(str(namespace["revision"])), 32)

    def test_initial_migration_upgrades_and_downgrades_clean_database(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "migration.sqlite3"
            config = Config(str(ROOT / "alembic.ini"))
            config.set_main_option("script_location", str(ROOT / "migrations"))
            config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")

            with patch.dict(os.environ, {"SALAMANDRA_DATABASE_URL": ""}):
                command.upgrade(config, "head")
                engine = create_engine(f"sqlite:///{database_path.as_posix()}")
                inspector = sqlalchemy_inspect(engine)
                upgraded_tables = set(inspector.get_table_names())
                event_checks = {
                    constraint["name"]
                    for constraint in inspector.get_check_constraints("events")
                }
                movement_uniques = {
                    constraint["name"]
                    for constraint in inspector.get_unique_constraints("stock_movements")
                }
                holding_indexes = {
                    index["name"]
                    for index in inspector.get_indexes("inventory_holdings")
                }
                engine.dispose()

                command.downgrade(config, "base")
                engine = create_engine(f"sqlite:///{database_path.as_posix()}")
                downgraded_tables = set(sqlalchemy_inspect(engine).get_table_names())
                engine.dispose()

        expected_tables = {
            "catalog_terms",
            "alembic_version",
            "organizations",
            "users",
            "memberships",
            "inventory_holdings",
            "events",
            "item_class_records",
            "integration_connections",
            "allocations",
            "allocation_lines",
            "stock_movements",
            "audit_events",
            "sessions",
            "saved_kits",
            "operation_requests",
            "inventory_adjustments",
        }
        revision_source = (
            ROOT / "migrations" / "versions" / "0001_transactional_core.py"
        ).read_text(encoding="utf-8")

        self.assertEqual(upgraded_tables, expected_tables)
        self.assertIn("ck_events_status", event_checks)
        self.assertIn("uq_movements_idempotency", movement_uniques)
        self.assertIn("ix_holdings_org_item", holding_indexes)
        self.assertNotIn("Base.metadata", revision_source)
        self.assertNotIn("from database import", revision_source)
        self.assertEqual(downgraded_tables, {"alembic_version"})


if __name__ == "__main__":
    unittest.main()
