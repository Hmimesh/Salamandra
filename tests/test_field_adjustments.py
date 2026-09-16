import unittest
from copy import deepcopy
from unittest.mock import patch

from sqlalchemy import func, select
import test_equipment_conditions as conditions
from acceptance_support import postgres_required
from database import (AllocationLineModel, EventModel, FieldAdjustmentModel, InventoryHoldingModel,
    MembershipModel, StockMovementModel, TransactionalEventOperations)
from event_returns import EventReturns
from field_adjustments import FieldAdjustments
from security import AccessDenied, ResourceNotFound, StateConflict


class AdjustmentCases:
    seed = conditions.ConditionCases.seed
    dispatched_event = conditions.ConditionCases.dispatched_event

    def setup_event(self, packed=False):
        with self.factory.begin() as session:
            holding = session.get(InventoryHoldingModel, "stock")
            holding.data = {"display_name": "Cart \u05e2\u05d2\u05dc\u05d4", "type": "transport", "class_id": "utility-cart", "capabilities": ["transport.cart"]}
            session.add(EventModel(id="return-event", organization_id="a", owner_user_id="a", title="Field event", status="planning",
                data={"plan_verified": True, "plan": {"lines": [{"item_id": "mic", "amount": 6, "missing": 0}]}}))
        operations = TransactionalEventOperations(self.factory)
        for status in (("confirmed", "packed") if packed else ("confirmed", "packed", "out")):
            operations.transition("a", "return-event", status, "a", "fixture")
        self.returns = EventReturns(self.factory)
        self.adjustments = FieldAdjustments(self.factory)

    def request(self, removals=None):
        with self.factory() as session:
            version = session.get(EventModel, "return-event").version
        return {"event_id": "return-event", "version": version, "reason": "Onsite \u05d1\u05e7\u05e9\u05d4 \u0639\u0631\u0628\u064a", "idempotency_key": "requested",
            "requirements": [] if removals else [{"capability": "transport.cart", "amount": 2}], "removals": removals or []}

    def preview_body(self, row):
        preview = self.adjustments.preview("a", "a", {"event_id": "return-event", "adjustment_id": row["adjustment"]["id"]})
        return {"event_id": "return-event", "adjustment_id": row["adjustment"]["id"], "version": preview["event_version"],
            "adjustment_version": preview["adjustment_version"], "preview_token": preview["preview_token"], "idempotency_key": "fulfilled"}

    def test_supplemental_dispatch_then_inspected_return_preserves_original(self):
        self.setup_event()
        body = self.request()
        requested = self.adjustments.create("a", "a", body, "test")
        self.assertEqual(self.adjustments.create("a", "a", body, "retry"), requested)
        with self.factory() as session:
            holding = session.get(InventoryHoldingModel, "stock")
            self.assertEqual((holding.available_quantity, holding.dispatched_quantity), (4, 6))
            original = deepcopy(session.scalar(select(StockMovementModel).where(StockMovementModel.action == "out")).lines)
        fulfillment = self.preview_body(requested)
        result = self.adjustments.act("a", "a", fulfillment, "test")
        self.assertEqual(self.adjustments.act("a", "a", fulfillment, "retry"), result)
        with self.factory() as session:
            holding = session.get(InventoryHoldingModel, "stock")
            self.assertEqual((holding.available_quantity, holding.dispatched_quantity), (2, 8))
            self.assertEqual(session.scalar(select(StockMovementModel).where(StockMovementModel.action == "out")).lines, original)
            self.assertEqual(session.scalar(select(func.count()).select_from(StockMovementModel).where(StockMovementModel.action == "supplemental_dispatch")), 1)
        returns = EventReturns(self.factory)
        preview = returns.preview("a", "a", "return-event")
        self.assertEqual(preview["lines"][0]["quantity"], 8)
        returns.reconcile("a", "a", {"event_id": "return-event", "version": preview["version"], "idempotency_key": "inspected",
            "lines": [{"holding_id": "stock", "ready": 5, "damaged": 2, "missing": 1, "reason": "Inspection"}]}, "test")
        with self.factory() as session:
            holding = session.get(InventoryHoldingModel, "stock")
            self.assertEqual((holding.available_quantity, holding.condition_quantity, holding.dispatched_quantity), (7, 3, 0))

    def test_packed_release_is_retained_and_never_dispatched_or_returned(self):
        self.setup_event(packed=True)
        requested = self.adjustments.create("a", "a", self.request([{"holding_id": "stock", "quantity": 2}]), "test")
        self.adjustments.act("a", "a", self.preview_body(requested), "test")
        TransactionalEventOperations(self.factory).transition("a", "return-event", "out", "a", "test", "actual-out")
        preview = EventReturns(self.factory).preview("a", "a", "return-event")
        self.assertEqual(preview["lines"][0]["quantity"], 4)
        with self.factory() as session:
            holding = session.get(InventoryHoldingModel, "stock")
            self.assertEqual((holding.available_quantity, holding.dispatched_quantity, holding.packed_quantity), (6, 4, 0))
            self.assertEqual(session.scalar(select(AllocationLineModel).where(AllocationLineModel.state == "released")).quantity, 2)

    def test_dispatched_reduction_waits_for_return_and_pending_addition_closes(self):
        self.setup_event()
        requested = self.adjustments.create("a", "a", self.request([{"holding_id": "stock", "quantity": 2}]), "test")
        with self.assertRaises(StateConflict):
            self.adjustments.act("a", "a", self.preview_body(requested), "test")
        addition = self.adjustments.create("a", "a", {**self.request(), "idempotency_key": "extra"}, "test")
        preview = self.returns.preview("a", "a", "return-event")
        self.returns.reconcile("a", "a", {"event_id": "return-event", "version": preview["version"], "idempotency_key": "done",
            "lines": [{"holding_id": "stock", "ready": 6, "damaged": 0, "missing": 0}]}, "test")
        with self.factory() as session:
            self.assertEqual(session.get(FieldAdjustmentModel, requested["adjustment"]["id"]).status, "fulfilled")
            self.assertEqual(session.get(FieldAdjustmentModel, addition["adjustment"]["id"]).status, "cancelled")

    def test_scope_stale_role_and_conflicting_retry(self):
        self.setup_event()
        body = self.request()
        for identity in ("return-event", "absent"):
            with self.assertRaises(ResourceNotFound):
                self.adjustments.create("b", "b", {**body, "event_id": identity}, "test")
        result = self.adjustments.create("a", "a", body, "test")
        with self.assertRaises(StateConflict):
            self.adjustments.create("a", "a", {**body, "reason": "different"}, "test")
        with self.assertRaises(StateConflict):
            self.adjustments.create("a", "a", {**body, "idempotency_key": "stale"}, "test")
        fulfillment = self.preview_body(result)
        with self.factory.begin() as session:
            session.get(MembershipModel, "a").role = "technician"
        with self.assertRaises(AccessDenied):
            self.adjustments.act("a", "a", fulfillment, "test")

    def test_fulfillment_failure_rolls_back_and_stock_change_requires_review(self):
        self.setup_event()
        result = self.adjustments.create("a", "a", self.request(), "test")
        body = self.preview_body(result)
        with patch.object(FieldAdjustments, "_audit", side_effect=RuntimeError("Injected failure")):
            with self.assertRaises(RuntimeError):
                self.adjustments.act("a", "a", body, "test")
        with self.factory.begin() as session:
            holding = session.get(InventoryHoldingModel, "stock")
            self.assertEqual((holding.available_quantity, holding.dispatched_quantity), (4, 6))
            holding.available_quantity = 0
            holding.condition_quantity = 4
        with self.assertRaises(StateConflict):
            self.adjustments.act("a", "a", body, "test")

    def test_completed_dispatch_retry_rechecks_current_permission(self):
        self.setup_event()
        result = self.adjustments.create("a", "a", self.request(), "test")
        body = self.preview_body(result)
        self.adjustments.act("a", "a", body, "test")
        with self.factory.begin() as session:
            session.get(MembershipModel, "a").role = "technician"
        with self.assertRaises(AccessDenied):
            self.adjustments.act("a", "a", body, "retry")


class TestAdjustmentsSQLite(AdjustmentCases, unittest.TestCase):
    setUp = conditions.TestConditionsSQLite.setUp


@postgres_required
class TestAdjustmentsPostgres(AdjustmentCases, unittest.TestCase):
    setUp = conditions.TestConditionsPostgres.setUp
