import os
from pathlib import Path
import sys
import unittest
from uuid import uuid4

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from acceptance_support import postgres_required
from database import (Base, OrganizationModel, UserModel, MembershipModel, InventoryHoldingModel,
    ConditionIncidentModel, ConditionMovementModel, AuditEventModel, TransactionalInventoryOperations,
    EventModel, AllocationModel, TransactionalEventOperations)
from equipment_conditions import EquipmentConditions
from event_returns import EventReturns
from database import StockMovementModel, EventLearningRecordModel
from unittest.mock import patch
from postgres_removal import InventoryRemoval
from security import AccessDenied, ResourceNotFound, StateConflict
from test_database_phase2 import sqlite_factory


class ConditionCases:
    def dispatched_event(self):
        with self.factory.begin() as session:
            session.add(EventModel(id="return-event", organization_id="a", owner_user_id="a", title="Return event", status="planning",
                data={"source_type": "real", "plan_verified": True, "plan": {"lines": [{"item_id": "mic", "amount": 6, "missing": 0}]}}))
        operations = TransactionalEventOperations(self.factory)
        for status in ("confirmed", "packed", "out"):
            operations.transition("a", "return-event", status, "a", "request")
        self.returns = EventReturns(self.factory)
        preview = self.returns.preview("a", "a", "return-event")
        return {"event_id": "return-event", "version": preview["version"], "idempotency_key": "split-return",
            "lines": [{"holding_id": "stock", "ready": 3, "damaged": 2, "missing": 1, "reason": "Inspection \u05ea\u05d9\u05e7\u05d5\u05df \u0639\u0631\u0628\u064a"}]}

    def test_split_return_preserves_dispatch_learning_and_exact_stock(self):
        body = self.dispatched_event()
        with self.factory() as session:
            before = session.scalar(select(StockMovementModel).where(StockMovementModel.action == "out")).lines
        result = self.returns.reconcile("a", "a", body, "request")
        self.assertEqual(self.returns.reconcile("a", "a", body, "retry"), result)
        with self.factory() as session:
            holding = session.get(InventoryHoldingModel, "stock")
            self.assertEqual((holding.available_quantity, holding.dispatched_quantity, holding.condition_quantity), (7, 0, 3))
            self.assertEqual(TransactionalInventoryOperations._total(holding), 10)
            self.assertEqual(session.scalar(select(StockMovementModel).where(StockMovementModel.action == "out")).lines, before)
            returned = session.scalars(select(StockMovementModel).where(StockMovementModel.action == "returned")).all()
            self.assertEqual(len(returned), 1)
            self.assertEqual(returned[0].lines[0]["ready"], 3)
            incidents = session.scalars(select(ConditionIncidentModel)).all()
            self.assertEqual({(r.status, r.quantity, r.event_id) for r in incidents}, {("needs_repair", 2, "return-event"), ("missing", 1, "return-event")})
            evidence = session.scalar(select(EventLearningRecordModel).where(EventLearningRecordModel.event_id == "return-event"))
            self.assertEqual(evidence.execution["returned"], [returned[0].lines])
        with self.assertRaises(StateConflict):
            self.returns.reconcile("a", "a", {**body, "idempotency_key": "different"}, "request")

    def test_split_return_validates_scope_version_quantities_and_role(self):
        body = self.dispatched_event()
        for invalid in ({**body, "version": 1}, {**body, "lines": []},
                {**body, "lines": [{**body["lines"][0], "ready": 9}]}):
            with self.assertRaises(StateConflict):
                self.returns.reconcile("a", "a", invalid, "request")
        for identity in ("return-event", "absent"):
            with self.assertRaises(ResourceNotFound):
                self.returns.reconcile("b", "b", {**body, "event_id": identity}, "request")
        with self.factory.begin() as session:
            session.get(MembershipModel, "a").role = "read_only"
        with self.assertRaises(AccessDenied):
            self.returns.reconcile("a", "a", body, "request")
        with self.factory() as session:
            self.assertEqual(session.get(InventoryHoldingModel, "stock").dispatched_quantity, 6)
            self.assertEqual(session.get(EventModel, "return-event").status, "out")

    def test_split_return_failure_rolls_back_every_side_effect(self):
        body = self.dispatched_event()
        with patch.object(EquipmentConditions, "_move", side_effect=RuntimeError("injected failure")):
            with self.assertRaises(RuntimeError):
                self.returns.reconcile("a", "a", body, "request")
        with self.factory() as session:
            self.assertEqual(session.get(EventModel, "return-event").status, "out")
            self.assertEqual(session.get(InventoryHoldingModel, "stock").available_quantity, 4)
            self.assertEqual(session.scalar(select(func.count()).select_from(ConditionIncidentModel)), 0)
            self.assertEqual(session.scalar(select(func.count()).select_from(StockMovementModel).where(StockMovementModel.action == "returned")), 0)
        self.assertEqual(self.returns.reconcile("a", "a", body, "retry")["status"], "returned")

    def seed(self):
        with self.factory.begin() as session:
            for name in ("a", "b"):
                session.add(OrganizationModel(id=name, name=name))
                session.add(UserModel(id=name, name=name, email=f"{name}@example.test", password_hash="unused"))
                session.flush()
                session.add(MembershipModel(id=name, organization_id=name, user_id=name, role="owner"))
            session.flush()
            session.add(InventoryHoldingModel(id="stock", organization_id="a", scope="shared", legacy_item_id="mic", available_quantity=10,
                data={"display_name": "SM58 \u05de\u05d9\u05e7\u05e8\u05d5\u05e4\u05d5\u05df"}))
        self.service = EquipmentConditions(self.factory)
        self.body = {"item_id": "mic", "quantity": 2, "status": "needs_repair", "issue": "Intermittent output", "idempotency_key": "report"}

    def test_repair_lifecycle_idempotency_ownership_and_ledger(self):
        report = self.service.report("a", "a", self.body, "request")
        self.assertEqual(self.service.report("a", "a", self.body, "retry"), report)
        self.assertEqual(self.service.list("a", "a")["quantities"]["needs_repair"], 2)
        with self.factory() as session:
            holding = session.get(InventoryHoldingModel, "stock")
            self.assertEqual((holding.available_quantity, holding.condition_quantity), (8, 2))
            self.assertEqual(TransactionalInventoryOperations._total(holding), 10)
        repairing = self.service.transition("a", "a", {"incident_id": report["id"], "version": 1, "status": "in_repair", "reason": "Bench test", "idempotency_key": "repair"}, "request")
        self.assertEqual(self.service.list("a", "a")["quantities"]["in_repair"], 2)
        done_body = {"incident_id": report["id"], "version": repairing["version"], "status": "ready", "reason": "Connector replaced", "idempotency_key": "done"}
        done = self.service.transition("a", "a", done_body, "request")
        self.assertEqual(self.service.transition("a", "a", done_body, "retry"), done)
        self.assertEqual(self.service.list("a", "a")["incidents"], [])
        self.assertEqual(sum(self.service.list("a", "a")["quantities"].values()), 0)
        with self.factory() as session:
            holding = session.get(InventoryHoldingModel, "stock")
            self.assertEqual((holding.available_quantity, holding.condition_quantity), (10, 0))
            self.assertEqual(session.scalar(select(func.count()).select_from(ConditionMovementModel)), 3)
            self.assertEqual(session.scalar(select(func.count()).select_from(AuditEventModel)), 3)

    def test_summary_counts_units_beyond_page_limit_without_private_or_tenant_leaks(self):
        with self.factory.begin() as session:
            holding = session.get(InventoryHoldingModel, "stock")
            holding.available_quantity, holding.condition_quantity = 0, 202
            session.add(MembershipModel(id="private-member", organization_id="a", user_id="b", role="technician"))
            session.flush()
            for identity, org, scope, owner in (("foreign", "b", "shared", None), ("private", "a", "personal", "b")):
                session.add(InventoryHoldingModel(id=identity, organization_id=org, scope=scope,
                    owner_user_id=owner, legacy_item_id=identity, available_quantity=0, condition_quantity=99))
            session.flush()
            for index in range(101):
                session.add(ConditionIncidentModel(organization_id="a", holding_id="stock", quantity=2,
                    status="needs_repair", issue=f"Fixture batch {index}"))
            for identity, org in (("foreign", "b"), ("private", "a")):
                session.add(ConditionIncidentModel(organization_id=org, holding_id=identity, quantity=99,
                    status="missing", issue="Private fixture"))
        result = self.service.list("a", "a")
        self.assertEqual(len(result["incidents"]), 100)
        self.assertEqual(result["quantities"], {"needs_repair": 202, "in_repair": 0, "quarantine": 0, "missing": 0, "retired": 0})
        filtered = self.service.list("a", "a", "missing")
        self.assertEqual(filtered["incidents"], [])
        self.assertEqual(filtered["quantities"], result["quantities"])
        self.assertEqual(self.service.list("b", "b")["quantities"]["missing"], 99)

    def test_invalid_quantity_and_transition_roll_back(self):
        for amount in (0, -1, True, 1.5, 10001):
            with self.assertRaises(ValueError):
                self.service.report("a", "a", {**self.body, "quantity": amount}, "request")
        with self.assertRaises(StateConflict):
            self.service.report("a", "a", {**self.body, "quantity": 11}, "request")
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(ConditionIncidentModel)), 0)
            self.assertEqual(session.get(InventoryHoldingModel, "stock").available_quantity, 10)
        row = self.service.report("a", "a", self.body, "request")
        with self.assertRaises(StateConflict):
            self.service.transition("a", "a", {"incident_id": row["id"], "version": 1, "status": "ready", "reason": "skip repair", "idempotency_key": "skip"}, "request")

    def test_tenant_role_and_clear_safety(self):
        row = self.service.report("a", "a", self.body, "request")
        for identity in (row["id"], "absent"):
            with self.assertRaises(ResourceNotFound):
                self.service.transition("b", "b", {"incident_id": identity, "version": 1, "status": "in_repair", "reason": "x", "idempotency_key": identity}, "request")
        self.assertEqual(self.service.list("b", "b")["incidents"], [])
        removal = InventoryRemoval(self.factory)
        preview = removal.preview("a", "a")
        self.assertTrue(preview["blocked"])
        with self.assertRaises(StateConflict):
            removal.execute("a", "a", {"confirmation": "CLEAR INVENTORY", "preview_token": preview["preview_token"], "idempotency_key": "clear"}, "request", clear=True)
        with self.factory.begin() as session:
            session.get(MembershipModel, "a").role = "read_only"
        with self.assertRaises(AccessDenied):
            self.service.report("a", "a", self.body, "request")
        with self.assertRaises(AccessDenied):
            self.service.list("a", "a")

    def test_nonready_stock_cannot_satisfy_reservation(self):
        self.service.report("a", "a", {**self.body, "quantity": 9}, "request")
        with self.factory.begin() as session:
            session.add(EventModel(id="event", organization_id="a", owner_user_id="a", title="Event", status="planning",
                data={"plan_verified": True, "plan": {"lines": [{"item_id": "mic", "amount": 2, "missing": 0}]}}))
        with self.assertRaises(StateConflict):
            TransactionalEventOperations(self.factory).transition("a", "event", "confirmed", "a", "request")
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(AllocationModel)), 0)
            holding = session.get(InventoryHoldingModel, "stock")
            self.assertEqual((holding.available_quantity, holding.reserved_quantity, holding.condition_quantity), (1, 0, 9))

    def test_other_members_personal_stock_is_not_available_to_managers(self):
        with self.factory.begin() as session:
            session.add(MembershipModel(id="b-in-a", organization_id="a", user_id="b", role="technician"))
            session.flush()
            session.add(InventoryHoldingModel(id="private", organization_id="a", scope="personal", owner_user_id="b", legacy_item_id="private-mic", available_quantity=1))
        private = {**self.body, "scope": "personal", "item_id": "private-mic", "quantity": 1}
        with self.assertRaises(ResourceNotFound):
            self.service.report("a", "a", private, "request")
        reported = self.service.report("a", "b", private, "request")
        self.assertEqual(self.service.list("a", "a")["incidents"], [])
        with self.assertRaises(ResourceNotFound):
            self.service.transition("a", "a", {"incident_id": reported["id"], "version": 1, "status": "in_repair", "reason": "x", "idempotency_key": "private"}, "request")


class TestConditionsSQLite(ConditionCases, unittest.TestCase):
    def setUp(self):
        self.engine, self.factory = sqlite_factory()
        self.addCleanup(self.engine.dispose)
        self.seed()


@postgres_required
class TestConditionsPostgres(ConditionCases, unittest.TestCase):
    def setUp(self):
        url = os.environ["SALAMANDRA_TEST_POSTGRES_URL"]
        schema = "conditions_" + uuid4().hex
        admin = create_engine(url, isolation_level="AUTOCOMMIT")
        self.addCleanup(admin.dispose)
        with admin.connect() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        def drop():
            with admin.connect() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        self.addCleanup(drop)
        self.engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.seed()
