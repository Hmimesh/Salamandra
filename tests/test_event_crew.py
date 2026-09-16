import json
import unittest
from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import select, func
import test_equipment_conditions as conditions
from acceptance_support import postgres_required, race_required
from database import (EventModel, CrewAssignmentModel, CrewAccessModel, CrewProfileModel, CrewSkillModel,
    MembershipModel, OperationRequestModel, AuditEventModel, InventoryHoldingModel, utc_now)
from event_crew import EventCrew
from security import AccessDenied, ResourceNotFound, StateConflict


class CrewCases:
    seed = conditions.ConditionCases.seed

    def setup_crew(self):
        self.crew = EventCrew(self.factory)
        with self.factory.begin() as session:
            for identity in ("event", "second"):
                session.add(EventModel(id=identity, organization_id="a", owner_user_id="a", title="Show " + identity,
                    status="planning", data={"start_date": "2027-09-20", "start_time": "18:00", "duration_minutes": 120,
                        "location": "Venue", "history": [], "private_note": "Never external"}))
        self.profile_body = {"name": "\u05d9\u05d5\u05e1\u05d9", "kind": "external", "complexity": 3,
            "contact": "private@example.test", "notes": "Internal only", "skills": {"FOH": 3, "live_band": 2}, "idempotency_key": "profile"}
        self.profile = self.crew.profile("a", "a", self.profile_body, "test")
        self.role = self.crew.role("a", "a", {"event_id": "event", "event_version": 1, "name": "FOH", "quantity": 2,
            "complexity": 3, "skills": {"foh": 2}, "idempotency_key": "role"}, "test")
        self.assignment_body = {"event_id": "event", "event_version": 2, "profile_id": self.profile["id"],
            "role_id": self.role["id"], "call_at": "2027-09-20T12:00+03:00", "release_at": "2027-09-20T23:00+03:00",
            "notes": "\u0639\u0631\u0628\u064a English \u05e9\u05dc\u05d5\u05dd", "equipment": [], "idempotency_key": "assign"}

    def test_profile_skills_assignment_unicode_audit_and_no_stock_mutation(self):
        self.setup_crew()
        self.assertEqual(self.crew.profile("a", "a", self.profile_body, "retry"), self.profile)
        profile = self.crew.profiles("a", "a")[0]
        self.assertEqual(profile["name"], self.profile_body["name"])
        self.assertEqual(profile["skills"], {"foh": 3, "live_band": 2})
        result = self.crew.assign("a", "a", self.assignment_body, "test")
        self.assertEqual(self.crew.assign("a", "a", self.assignment_body, "retry"), result)
        self.assertEqual(self.crew.event("a", "a", "event")["assignments"][0]["notes"], self.assignment_body["notes"])
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(CrewAssignmentModel)), 1)
            self.assertEqual(session.get(InventoryHoldingModel, "stock").available_quantity, 10)
            self.assertEqual(session.scalar(select(func.count()).select_from(AuditEventModel).where(AuditEventModel.action == "crew.assign")), 1)

    def test_conflicts_adjacent_windows_skills_and_audited_override(self):
        self.setup_crew()
        self.crew.assign("a", "a", self.assignment_body, "test")
        second_role = self.crew.role("a", "a", {"event_id": "second", "event_version": 1, "name": "Stage", "skills": {"rf": 3}, "idempotency_key": "role2"}, "test")
        body = {**self.assignment_body, "event_id": "second", "role_id": second_role["id"], "idempotency_key": "second"}
        with self.assertRaisesRegex(StateConflict, "overlaps"):
            self.crew.assign("a", "a", body, "test")
        with self.assertRaisesRegex(StateConflict, "skills"):
            self.crew.assign("a", "a", {**body, "call_at": "2027-09-20T23:00+03:00", "release_at": "2027-09-21T01:00+03:00"}, "test")
        with self.factory.begin() as session:
            session.get(MembershipModel, "a").role = "producer"
        with self.assertRaises(AccessDenied):
            self.crew.assign("a", "a", {**body, "override_reason": "Approved cover"}, "test")
        with self.factory.begin() as session:
            session.get(MembershipModel, "a").role = "operator"
        result = self.crew.assign("a", "a", {**body, "override_reason": "Approved cover"}, "test")
        self.assertEqual(len(result["warnings"]), 2)
        with self.factory() as session:
            audit = session.scalar(select(AuditEventModel).where(AuditEventModel.resource_id == result["id"]))
            self.assertEqual(audit.changes["override_reason"], "Approved cover")

    def test_scope_role_forgery_stale_and_conflicting_keys(self):
        self.setup_crew()
        for identity in ("event", "absent"):
            with self.assertRaises(ResourceNotFound):
                self.crew.event("b", "b", identity)
            with self.assertRaises(ResourceNotFound):
                self.crew.role("b", "b", {"event_id": identity, "event_version": 2, "name": "Guess", "idempotency_key": "guess-role"}, "test")
            with self.assertRaises(ResourceNotFound):
                self.crew.assign("b", "b", {**self.assignment_body, "event_id": identity}, "test")
        self.assertEqual(self.crew.profiles("b", "b"), [])
        with self.assertRaises(ResourceNotFound):
            self.crew.profile("b", "b", {**self.profile_body, **self.profile}, "test")
        with self.assertRaises(ValueError):
            self.crew.assign("a", "a", {**self.assignment_body, "organization_id": "b", "role": "owner"}, "test")
        result = self.crew.assign("a", "a", self.assignment_body, "test")
        for body in ({**self.assignment_body, "notes": "conflict"}, {**self.assignment_body, "idempotency_key": "stale"}):
            with self.assertRaises(StateConflict):
                self.crew.assign("a", "a", body, "test")
        with self.factory.begin() as session:
            session.get(MembershipModel, "a").role = "technician"
        for command, body in ((self.crew.profile, self.profile_body), (self.crew.assign, self.assignment_body),
                (self.crew.access, {"assignment_id": result["id"], "version": 1, "action": "issue", "idempotency_key": "access"})):
            with self.assertRaises(AccessDenied):
                command("a", "a", body, "test")

    def test_external_hash_only_replay_rotation_revoke_expiry_and_projection(self):
        self.setup_crew()
        assignment = self.crew.assign("a", "a", self.assignment_body, "test")
        body = {"assignment_id": assignment["id"], "version": 1, "action": "issue", "idempotency_key": "access"}
        access = self.crew.access("a", "a", body, "test")
        self.assertEqual(len(access["token"]), 43)
        self.assertIsNone(self.crew.access("a", "a", body, "retry")["token"])
        view = self.crew.external(access["token"])
        self.assertEqual(view["notes"], self.assignment_body["notes"])
        self.assertEqual(view["equipment"], [])
        serialized = json.dumps(view)
        for private in ("private@example.test", "Internal only", "Never external", "organization_id", "profile_id"):
            self.assertNotIn(private, serialized)
        with self.factory() as session:
            self.assertNotIn(access["token"], str([r.response for r in session.scalars(select(OperationRequestModel))]))
            self.assertNotEqual(session.scalar(select(CrewAccessModel)).token_hash, access["token"])
        rotated = self.crew.access("a", "a", {**body, "version": 2, "idempotency_key": "rotate"}, "test")
        with self.assertRaises(ResourceNotFound):
            self.crew.external(access["token"])
        self.crew.access("a", "a", {**body, "version": 3, "action": "revoke", "idempotency_key": "revoke"}, "test")
        with self.assertRaises(ResourceNotFound):
            self.crew.external(rotated["token"])
        issued = self.crew.access("a", "a", {**body, "version": 4, "idempotency_key": "new"}, "test")
        with self.factory.begin() as session:
            for row in session.scalars(select(CrewAccessModel)):
                row.expires_at = utc_now() - timedelta(seconds=1)
        with self.assertRaises(ResourceNotFound):
            self.crew.external(issued["token"])
        for token in ("event", "a" * 43, ""):
            with self.assertRaises(ResourceNotFound):
                self.crew.external(token)

    def test_assignment_edits_do_not_extend_access_and_inactive_profile_revokes(self):
        self.setup_crew()
        assigned = self.crew.assign("a", "a", self.assignment_body, "test")
        access = self.crew.access("a", "a", {"assignment_id": assigned["id"], "version": 1, "action": "issue", "idempotency_key": "access"}, "test")
        changed = self.crew.assign("a", "a", {**self.assignment_body, "id": assigned["id"], "version": 2,
            "event_version": 3, "release_at": "2027-09-21T01:00+03:00", "notes": "Updated note", "idempotency_key": "edit"}, "test")
        self.assertEqual(self.crew.external(access["token"])["notes"], "Updated note")
        with self.factory() as session:
            from event_crew import aware
            self.assertEqual(aware(session.scalar(select(CrewAccessModel)).expires_at).isoformat(), access["expires_at"])
        self.crew.profile("a", "a", {**self.profile_body, **self.profile, "active": False, "idempotency_key": "disable"}, "test")
        with self.assertRaises(ResourceNotFound):
            self.crew.external(access["token"])
        self.crew.profile("a", "a", {**self.profile_body, **self.profile, "version": 2, "active": True, "idempotency_key": "enable"}, "test")
        with self.assertRaises(ResourceNotFound):
            self.crew.external(access["token"])
        self.assertEqual(changed["version"], 3)
        renewed = self.crew.access("a", "a", {"assignment_id": assigned["id"], "version": 3, "action": "issue", "idempotency_key": "renew"}, "test")
        with self.factory.begin() as session:
            session.get(EventModel, "event").status = "cancelled"
        with self.assertRaises(ResourceNotFound):
            self.crew.external(renewed["token"])

    def test_rollback_and_closed_event(self):
        self.setup_crew()
        with patch("event_crew.audit", side_effect=RuntimeError("injected")):
            with self.assertRaises(RuntimeError):
                self.crew.assign("a", "a", self.assignment_body, "test")
        with self.factory.begin() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(CrewAssignmentModel)), 0)
            self.assertEqual(session.get(EventModel, "event").version, 2)
            session.get(EventModel, "event").status = "returned"
        with self.assertRaises(StateConflict):
            self.crew.assign("a", "a", self.assignment_body, "test")

    def test_adjacent_windows_are_not_conflicts_and_cancellation_preserves_history(self):
        self.setup_crew()
        first = self.crew.assign("a", "a", self.assignment_body, "test")
        second_role = self.crew.role("a", "a", {"event_id": "second", "event_version": 1, "name": "FOH", "idempotency_key": "second-role"}, "test")
        adjacent = {**self.assignment_body, "event_id": "second", "role_id": second_role["id"], "call_at": self.assignment_body["release_at"],
            "release_at": "2027-09-21T01:00+03:00", "idempotency_key": "adjacent"}
        self.assertEqual(self.crew.assign("a", "a", adjacent, "test")["warnings"], [])
        access = self.crew.access("a", "a", {"assignment_id": first["id"], "version": 1, "action": "issue", "idempotency_key": "link"}, "test")
        cancelled = self.crew.assign("a", "a", {**self.assignment_body, "id": first["id"], "version": 2, "event_version": 3,
            "status": "cancelled", "idempotency_key": "cancel-assignment"}, "test")
        self.assertEqual(cancelled["status"], "cancelled")
        with self.assertRaises(ResourceNotFound):
            self.crew.external(access["token"])
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(CrewAssignmentModel)), 2)

    def test_selected_equipment_logistics_and_inspected_return_remain_authoritative(self):
        from database import TransactionalEventOperations, ConditionIncidentModel, StockMovementModel
        from event_returns import EventReturns
        from event_logistics import EventLogistics
        self.setup_crew()
        with self.factory.begin() as session:
            event = session.get(EventModel, "event")
            event.data = {**event.data, "plan_verified": True, "plan": {"lines": [{"item_id": "mic", "amount": 2, "missing": 0}]}}
        operations = TransactionalEventOperations(self.factory)
        for status in ("confirmed", "packed", "out"):
            operations.transition("a", "event", status, "a", "test")
        current = self.crew.event("a", "a", "event")
        body = {**self.assignment_body, "event_version": current["event_version"], "equipment": ["stock"]}
        with self.assertRaises(ValueError):
            self.crew.assign("a", "a", {**body, "equipment": ["foreign-holding"]}, "test")
        assigned = self.crew.assign("a", "a", body, "test")
        access = self.crew.access("a", "a", {"assignment_id": assigned["id"], "version": 1, "action": "issue", "idempotency_key": "link"}, "test")
        view = self.crew.external(access["token"])
        self.assertEqual(view["equipment"][0]["quantity"], 2)
        schedule = EventLogistics(self.factory)
        updated = schedule.update("a", "a", {"event_id": "event", "version": assigned["event_version"], "milestones": {"return_due_at": "2027-09-21T10:00"},
            "notes": "private warehouse details", "idempotency_key": "logistics"}, "test")
        self.assertIn("return_due_at", self.crew.external(access["token"])["milestones"])
        self.assertNotIn("private warehouse details", json.dumps(self.crew.external(access["token"])))
        exported = schedule.export("a", "a", "event")
        self.assertIn(["Crew", "FOH", self.profile_body["name"]], exported)
        returns = EventReturns(self.factory)
        result = returns.reconcile("a", "a", {"event_id": "event", "version": updated["event_version"], "idempotency_key": "return",
            "lines": [{"holding_id": "stock", "ready": 1, "damaged": 1, "missing": 0, "reason": "Inspection"}]}, "test")
        self.assertEqual(self.crew.external(access["token"])["status"], "returned")
        with self.factory() as session:
            holding = session.get(InventoryHoldingModel, "stock")
            self.assertEqual((holding.available_quantity, holding.condition_quantity, holding.dispatched_quantity), (9, 1, 0))
            self.assertEqual(session.scalar(select(func.count()).select_from(ConditionIncidentModel)), 1)
            self.assertEqual(session.scalar(select(func.count()).select_from(StockMovementModel).where(StockMovementModel.action == "out")), 1)

    def test_operational_summary_separates_standby_and_condition_stock_by_scope(self):
        from database import TransactionalEventOperations
        from event_logistics import EventLogistics
        from equipment_conditions import EquipmentConditions
        self.setup_crew()
        with self.factory.begin() as session:
            event = session.get(EventModel, "event")
            event.data = {**event.data, "plan_verified": True, "plan": {"lines": [{"item_id": "mic", "amount": 2, "missing": 0}]}}
        operations = TransactionalEventOperations(self.factory)
        for status in ("confirmed", "packed"):
            operations.transition("a", "event", status, "a", "test")
        logistics = EventLogistics(self.factory)
        logistics.update("a", "a", {"event_id": "event", "version": logistics.get("a", "a", "event")["event_version"], "standby": True, "idempotency_key": "stage"}, "test", staging=True)
        conditions = EquipmentConditions(self.factory)
        conditions.report("a", "a", {"item_id": "mic", "quantity": 1, "status": "quarantine", "issue": "Inspection", "idempotency_key": "condition"}, "test")
        totals = conditions.summary("a", "a")
        self.assertEqual((totals["ready"], totals["packed"], totals["standby"], totals["quarantine"]), (7, 0, 2, 1))
        self.assertEqual(sum(totals.values()), 10)
        self.assertEqual(sum(conditions.summary("b", "b").values()), 0)
        self.assertEqual(sum(conditions.summary("a", "a", "personal").values()), 0)


class TestCrewSQLite(CrewCases, unittest.TestCase):
    setUp = conditions.TestConditionsSQLite.setUp


@postgres_required
class TestCrewPostgres(CrewCases, unittest.TestCase):
    setUp = conditions.TestConditionsPostgres.setUp

    @race_required
    def test_external_projection_keeps_one_committed_snapshot_during_edits(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        import event_crew
        self.setup_crew()
        assigned = self.crew.assign("a", "a", self.assignment_body, "test")
        access = self.crew.access("a", "a", {"assignment_id": assigned["id"], "version": 1, "action": "issue", "idempotency_key": "link"}, "test")
        reading, resume = threading.Event(), threading.Event()
        original = event_crew.scoped

        def pause(session, model, org, identity, lock=False):
            if model is CrewAssignmentModel and threading.current_thread() is not threading.main_thread() and not reading.is_set():
                reading.set()
                if not resume.wait(timeout=10):
                    raise TimeoutError("Snapshot test did not resume")
            return original(session, model, org, identity, lock)

        with patch("event_crew.scoped", side_effect=pause), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.crew.external, access["token"])
            try:
                self.assertTrue(reading.wait(timeout=5))
                self.crew.profile("a", "a", {**self.profile_body, **self.profile, "name": "Updated person", "idempotency_key": "profile-edit"}, "test")
                self.crew.assign("a", "a", {**self.assignment_body, "id": assigned["id"], "version": 2, "event_version": 3,
                    "notes": "Updated assignment", "idempotency_key": "assignment-edit"}, "test")
            finally:
                resume.set()
            snapshot = future.result(timeout=5)
        self.assertEqual(snapshot["person"], self.profile_body["name"])
        self.assertEqual(snapshot["notes"], self.assignment_body["notes"])
        current = self.crew.external(access["token"])
        self.assertEqual(current["person"], "Updated person")
        self.assertEqual(current["notes"], "Updated assignment")
