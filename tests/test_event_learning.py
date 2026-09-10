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

from database import EventModel, MembershipModel, OrganizationModel, UserModel
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
        result = self.store.set_eligibility("org-a", "user-org-a", "event-a", True, "", "r", None, "eligibility-1")
        self.assertTrue(result["eligible"])
        self.assertEqual(self.store.examples("org-a")[0]["event_id"], "event-a")


if __name__ == "__main__":
    unittest.main()
