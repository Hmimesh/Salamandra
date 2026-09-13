from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from database import EventModel, MembershipModel, OrganizationModel, UserModel, StockMovementModel
from event_learning import EventLearningStore
from test_database_phase2 import sqlite_factory
from security import AccessDenied, ResourceNotFound, StateConflict


class TestEventLearning(unittest.TestCase):
    def setUp(self):
        self.engine, self.factory = sqlite_factory()
        with self.factory.begin() as session:
            for org, role in (("org-a", "owner"), ("org-b", "owner")):
                session.add(OrganizationModel(id=org, name=org))
                session.add(UserModel(id=f"user-{org}", email=f"user@{org}.test", name=org, password_hash="test"))
                session.flush()
                session.add(MembershipModel(id=f"membership-{org}", organization_id=org, user_id=f"user-{org}", role=role))
            session.add(UserModel(id="operator-org-a", email="operator@org-a.test", name="Operator", password_hash="test"))
            session.flush()
            session.add(MembershipModel(id="membership-operator-org-a", organization_id="org-a", user_id="operator-org-a", role="operator"))
            session.flush()
            session.add(EventModel(id="event-a", organization_id="org-a", owner_user_id="user-org-a", title="אירוע عربي", status="returned", data={"description": "אירוע عربي 🎛️", "start_date": "2026-09-10", "capability_requirements": [{"capability": "pa.main", "amount": 2}], "plan": {"lines": [{"item_id": "sm58", "amount": 2}]}}))
            session.add(EventModel(id="event-b", organization_id="org-b", owner_user_id="user-org-b", title="Foreign", status="returned", data={"description": "Foreign", "plan": {"lines": []}}))
        self.store = EventLearningStore(self.factory)
        with self.factory.begin() as session:
            for event_id, org in (("event-a", "org-a"), ("event-b", "org-b")):
                event = session.get(EventModel, event_id)
                self.store.create_in_session(session, event, {"description": event.data["description"]})

    def tearDown(self):
        self.engine.dispose()

    def feedback(self, key="review-1", **extra):
        payload = {"missing": "no", "unnecessary": "no", "failed": "no", "additional_onsite": "no", "plan_fit": "about_right", "reuse_plan": "yes", "notes": "נשמר", "items": []}
        payload.update(extra)
        return self.store.save_feedback("org-a", "user-org-a", "event-a", payload, "request-1", key, None)

    def test_record_preserves_multilingual_brief_and_feedback_retry(self):
        record = self.store.get("org-a", "event-a")
        self.assertEqual(record["original_request"]["brief"], "אירוע عربي 🎛️")
        first = self.feedback()
        second = self.feedback()
        self.assertEqual(first, second)
        self.assertEqual(self.store.get("org-a", "event-a")["feedback"]["notes"], "נשמר")

    def test_conflicting_retry_and_invalid_state_are_rejected(self):
        self.feedback()
        with self.assertRaises(StateConflict):
            self.feedback(notes="different")
        with self.assertRaises(StateConflict):
            self.feedback(key="review-2", feedback_version=0)
        with self.factory.begin() as session:
            session.get(EventModel, "event-a").status = "planning"
        with self.assertRaises(StateConflict):
            self.feedback(key="review-2")

    def test_cross_org_reads_and_feedback_are_nondisclosing(self):
        with self.assertRaises(ResourceNotFound):
            self.store.get("org-a", "event-b")
        with self.assertRaises(ResourceNotFound):
            self.store.save_feedback("org-a", "user-org-a", "event-b", {"missing": "no"}, "r", "k", None)
        with self.assertRaises(ResourceNotFound):
            self.feedback(key="foreign-item", missing="yes", items=[{"kind": "missing", "item_id": "org-b-private", "quantity": 1, "note": ""}])
        with self.assertRaises(AccessDenied):
            self.store.set_eligibility("org-a", "operator-org-a", "event-a", True, "", "r", None, "eligibility-denied")

    def test_owner_can_include_only_returned_real_event(self):
        result = self.store.set_eligibility("org-a", "user-org-a", "event-a", True, "", "r", 1, "eligibility-1")
        self.assertTrue(result["eligible"])
        self.assertEqual(self.store.examples("org-a")[0]["event_id"], "event-a")

    def test_eligibility_stale_version_and_committed_replay(self):
        first = self.store.set_eligibility("org-a", "user-org-a", "event-a", True, "", "r", 1, "first")
        self.assertEqual(first["version"], 2)
        for value in (True, False):
            with self.assertRaises(StateConflict):
                self.store.set_eligibility("org-a", "user-org-a", "event-a", value, "", "r", 1, f"stale-{value}")
        self.store.set_eligibility("org-a", "user-org-a", "event-a", False, "excluded", "r", 2, "second")
        replay = self.store.set_eligibility("org-a", "user-org-a", "event-a", True, "", "r", 1, "first")
        self.assertEqual(replay, first)
        self.assertFalse(self.store.get("org-a", "event-a")["eligible"])

    def test_cancellation_synchronizes_learning_for_all_legal_states(self):
        from database import TransactionalEventOperations
        for status in ("planning", "confirmed", "packed"):
            with self.subTest(status=status):
                with self.factory.begin() as session:
                    event = session.get(EventModel, "event-a")
                    event.status = status
                    if status == "packed":
                        session.add(StockMovementModel(organization_id="org-a", event_id="event-a",
                            actor_membership_id="membership-org-a", action="packed", idempotency_key="packed",
                            lines=[{"holding_id": "holding-a", "quantity": 2}]))
                    session.flush()
                    self.store.sync_execution_in_session(session, event)
                TransactionalEventOperations(self.factory).cancel("org-a", "event-a", "user-org-a", "cancel")
                record = self.store.get("org-a", "event-a")
                self.assertEqual(record["execution"]["status"], "cancelled")
                self.assertFalse(record["eligible"])
                self.assertEqual(record["execution"]["returned"], [])
                if status == "packed":
                    self.assertEqual(record["execution"]["packed"], [[{"holding_id": "holding-a", "quantity": 2}]])
                self.assertEqual(self.store.examples("org-a"), [])

    def test_execution_preserves_every_ledger_stage(self):
        with self.factory.begin() as session:
            for action in ("packed", "out", "returned"):
                session.add(StockMovementModel(organization_id="org-a", event_id="event-a",
                    actor_membership_id="membership-org-a", action=action,
                    idempotency_key=action, lines=[{"holding_id": "holding-a", "quantity": 2}]))
            session.flush()
            self.store.sync_execution_in_session(session, session.get(EventModel, "event-a"))
        execution = self.store.get("org-a", "event-a")["execution"]
        for stage in ("packed", "dispatched", "returned"):
            self.assertEqual(execution[stage], [[{"holding_id": "holding-a", "quantity": 2}]])

    def test_metadata_edit_keeps_original_to_final_corrections_and_current_features(self):
        with self.factory.begin() as session:
            event = session.get(EventModel, "event-a")
            previous = dict(event.data)
            event.data = {**previous, "attendee_count": 110,
                "capability_requirements": [{"capability": "pa.main", "amount": 4}]}
            self.store.capture_edit_in_session(session, event, previous)
            previous = dict(event.data)
            event.data = {**previous, "title": "New title"}
            self.store.capture_edit_in_session(session, event, previous)
        record = self.store.get("org-a", "event-a")
        self.assertTrue(record["corrections"]["changed"])
        self.assertEqual(record["corrections"]["requirements"]["proposed"][0]["amount"], 2)
        self.assertEqual(record["corrections"]["requirements"]["final_planned"][0]["amount"], 4)
        self.assertEqual(record["features"]["guest_count"], 110)
        self.assertIsNone(record["original_request"]["features"]["guest_count"])


if __name__ == "__main__":
    unittest.main()
