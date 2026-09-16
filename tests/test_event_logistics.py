import unittest
from sqlalchemy import select
import test_equipment_conditions as conditions
from acceptance_support import postgres_required
from database import EventModel, InventoryHoldingModel, MembershipModel, TransactionalEventOperations
from event_logistics import EventLogistics, instant, logistics_window
from event_memory import EventRecord
from postgres_runtime import PostgresEventMemory
from security import AccessDenied, ResourceNotFound, StateConflict


class LogisticsCases:
    seed = conditions.ConditionCases.seed

    def setup_event(self):
        with self.factory.begin() as session:
            session.add(EventModel(id="event", organization_id="a", owner_user_id="a", title="Show", status="planning", data={
                "start_date": "2027-09-20", "start_time": "18:00", "duration_minutes": 300, "plan_verified": True,
                "plan": {"lines": [{"item_id": "mic", "amount": 2, "missing": 0}]}}))
        self.logistics = EventLogistics(self.factory)
        self.body = {"event_id": "event", "version": 1, "idempotency_key": "schedule", "notes": "\u05e2\u05d1\u05e8\u05d9\u05ea \u0639\u0631\u0628\u064a English",
            "milestones": {"prepare_at": "2027-09-19T10:00", "pack_by": "2027-09-19T15:00", "standby_at": "2027-09-19T17:00", "dispatch_at": "2027-09-20T10:00", "return_due_at": "2027-09-21T10:00"}}

    def test_window_is_separate_from_show_and_overlaps_earlier_day(self):
        self.setup_event()
        result = self.logistics.update("a", "a", self.body, "test")
        self.assertEqual(self.logistics.update("a", "a", self.body, "retry"), result)
        self.assertEqual(result["duration_minutes"], 300)
        with self.factory() as session:
            row = session.get(EventModel, "event")
            event = EventRecord.from_dict(row.data)
            self.assertEqual(event.start_date, "2027-09-20")
            self.assertTrue(PostgresEventMemory._events_overlap(event, "2027-09-19", "18:00", 60))
            self.assertFalse(PostgresEventMemory._events_overlap(event, "2027-09-21", "10:00", 60))
            self.assertIsNotNone(row.reservation_starts_at)
            self.assertEqual(session.get(InventoryHoldingModel, "stock").available_quantity, 10)
        exported = self.logistics.export("a", "a", "event")
        self.assertIn(["Logistics", "Notes", self.body["notes"]], exported)
        _, end = logistics_window({"start_date": "2027-09-20", "start_time": "18:00", "duration_minutes": 300,
            "logistics": {"milestones": {"teardown_at": "2027-09-21T01:00"}}})
        self.assertEqual(end, instant("2027-09-21T01:00"))

    def test_standby_preserves_packed_stock_and_freezes_completed_milestones(self):
        self.setup_event()
        self.logistics.update("a", "a", self.body, "test")
        operations = TransactionalEventOperations(self.factory)
        for status in ("confirmed", "packed"):
            operations.transition("a", "event", status, "a", "test")
        schedule = self.logistics.get("a", "a", "event")
        stage = {"event_id": "event", "version": schedule["event_version"], "idempotency_key": "stage", "standby": True}
        result = self.logistics.update("a", "a", stage, "test", staging=True)
        self.assertEqual(self.logistics.update("a", "a", stage, "retry", staging=True), result)
        self.assertTrue(result["standby"])
        with self.factory() as session:
            holding = session.get(InventoryHoldingModel, "stock")
            self.assertEqual((holding.available_quantity, holding.packed_quantity), (8, 2))
        changed = {**self.body, "version": result["event_version"], "idempotency_key": "rewrite",
            "milestones": {**self.body["milestones"], "pack_by": "2027-09-19T14:00"}}
        with self.assertRaises(StateConflict):
            self.logistics.update("a", "a", changed, "test")
        operations.transition("a", "event", "out", "a", "test")
        self.assertFalse(self.logistics.get("a", "a", "event")["standby"])

    def test_authorization_scope_versions_and_idempotency(self):
        self.setup_event()
        for identity in ("event", "absent"):
            with self.assertRaises(ResourceNotFound):
                self.logistics.update("b", "b", {**self.body, "event_id": identity}, "test")
            with self.assertRaises(ResourceNotFound):
                self.logistics.export("b", "b", identity)
        self.logistics.update("a", "a", self.body, "test")
        for body in ({**self.body, "notes": "different"}, {**self.body, "idempotency_key": "stale"}):
            with self.assertRaises(StateConflict):
                self.logistics.update("a", "a", body, "test")
        with self.factory.begin() as session:
            session.get(MembershipModel, "a").role = "technician"
        with self.assertRaises(AccessDenied):
            self.logistics.update("a", "a", self.body, "test")

    def test_invalid_chronology_and_ambiguous_local_time(self):
        self.setup_event()
        with self.assertRaises(ValueError):
            self.logistics.update("a", "a", {**self.body, "milestones": {"return_due_at": "2027-09-18T10:00"}}, "test")
        with self.assertRaises(ValueError):
            instant("2026-10-25T01:30")
        with self.assertRaises(ValueError):
            instant("2026-03-27T02:30")
        self.assertEqual(instant("2026-10-25T01:30+03:00").hour, 22)
        with self.factory() as session:
            self.assertEqual(session.get(EventModel, "event").version, 1)


class TestLogisticsSQLite(LogisticsCases, unittest.TestCase):
    setUp = conditions.TestConditionsSQLite.setUp


@postgres_required
class TestLogisticsPostgres(LogisticsCases, unittest.TestCase):
    setUp = conditions.TestConditionsPostgres.setUp
