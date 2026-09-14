from copy import deepcopy
import os
from pathlib import Path
import sys
import unittest
from uuid import uuid4

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from acceptance_support import postgres_required
from database import (Base, EventModel, EventLearningRecordModel, MembershipModel, OrganizationModel,
    UserModel, SuggestionSessionModel, SuggestionEvaluationModel, AuditEventModel)
from event_learning import EventLearningStore
from suggestion_outcomes import SuggestionOutcomes
from security import AccessDenied, ResourceNotFound, StateConflict
from test_database_phase2 import sqlite_factory

FEATURES = {"departments": ["furniture"], "guest_count": 100, "duration_minutes": 120, "venue_type": "indoor"}


class OutcomeCases:
    def seed(self):
        self.service = SuggestionOutcomes(self.factory)
        with self.factory.begin() as session:
            for org in ("a", "b"):
                session.add(OrganizationModel(id=org, name=org))
                session.add(UserModel(id=org, email=f"{org}@test.test", name=org, password_hash="unused"))
                session.flush()
                session.add(MembershipModel(id=org, user_id=org, organization_id=org, role="owner"))
            session.flush()
            for index in range(2):
                event = EventModel(id=f"history-{index}", organization_id="a", owner_user_id="a", status="returned", title="\u05e2\u05d1\u05e8\u05d9\u05ea \u0639\u0631\u0628\u064a",
                    data={"attendee_count": 100, "duration_minutes": 120, "venue_kind": "indoor", "description": "private notes",
                          "capability_requirements": [{"capability": "furniture.chair", "amount": 12}]})
                session.add(event)
                session.flush()
                row = EventLearningStore.create_in_session(session, event)
                row.eligible, row.exclusion_reason = True, None
            session.add(EventModel(id="draft", organization_id="a", owner_user_id="a", status="planning", title="Draft", data={}))

    def generate(self, **extra):
        return self.service.generate("a", "a", {"features": FEATURES, "idempotency_key": "generate", **extra}, "request")

    def action(self, identity, action="apply", quantities=None, key="respond", **extra):
        return self.service.interact("a", "a", {"session_id": identity, "action": action,
            "quantities": {"furniture.chair": 12} if quantities is None and action == "apply" else quantities or {},
            "idempotency_key": key, **extra}, "request")

    def test_snapshot_version_replay_and_no_private_notes(self):
        result = self.generate()
        self.assertEqual(result["algorithm_version"], "D1")
        self.assertEqual(result["retrieval_version"], "E1")
        self.assertNotIn("private notes", str(result))
        with self.factory.begin() as session:
            for row in session.scalars(select(EventLearningRecordModel)):
                row.eligible = False
        self.assertEqual(self.generate(), result)
        with self.assertRaises(StateConflict):
            self.generate(features={"departments": ["lighting"]})

    def test_apply_and_edited_and_ignored_are_immutable_idempotent(self):
        for index, (action, quantities, expected) in enumerate((("apply", {"furniture.chair": 12}, "applied"), ("apply", {"furniture.chair": 10}, "applied_with_edits"), ("ignored", {}, "ignored"))):
            identity = self.generate(idempotency_key=f"generate-{index}")["session_id"]
            result = self.action(identity, action, quantities, key=f"outcome-{index}")
            self.assertEqual(result["action"], expected)
            self.assertEqual(result, self.action(identity, action, quantities, key=f"outcome-{index}"))
            self.assertEqual(result, self.action(identity, action, quantities, key=f"new-key-{index}"))
            with self.assertRaises(StateConflict):
                self.action(identity, "apply", {"furniture.chair": 11}, key=f"conflict-{index}")
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(SuggestionSessionModel)), 3)
            self.assertEqual(session.scalar(select(func.count()).select_from(AuditEventModel).where(AuditEventModel.action.like("learning.suggestion.%"))), 3)

    def test_cross_org_and_other_actor_are_nondisclosing(self):
        identity = self.generate()["session_id"]
        for value in (identity, "nonexistent"):
            with self.assertRaises(ResourceNotFound):
                self.service.interact("b", "b", {"session_id": value, "action": "ignored", "idempotency_key": value}, "r")
        self.assertEqual(self.service.summary("b", "b")["sessions"], 0)
        with self.factory.begin() as session:
            session.get(MembershipModel, "a").role = "technician"
        with self.assertRaises(AccessDenied):
            self.action(identity)
        with self.assertRaises(AccessDenied):
            self.service.summary("a", "a")

    def test_stale_event_cannot_accept_new_response(self):
        identity = self.generate(event_id="draft", event_version=1)["session_id"]
        with self.factory.begin() as session:
            session.get(EventModel, "draft").version = 2
        with self.assertRaises(StateConflict):
            self.action(identity, event_version=1)
        with self.factory() as session:
            self.assertIsNone(session.get(SuggestionSessionModel, identity).action)

    def test_return_comparison_versions_preserve_initial_snapshot(self):
        identity = self.generate()["session_id"]
        self.action(identity, quantities={"furniture.chair": 10})
        with self.factory.begin() as session:
            event = session.get(EventModel, "draft")
            event.data = {"capability_requirements": [{"capability": "furniture.chair", "amount": 10}]}
            SuggestionOutcomes.link_in_session(session, event, "a", identity)
            event.status = "returned"
            row = EventLearningStore.create_in_session(session, event)
            row.execution = {"returned": [[{"item_id": "chair", "quantity": 10}]]}
        self.service.evaluate("a", "a", "draft")
        self.service.evaluate("a", "a", "draft")
        with self.factory() as session:
            initial = deepcopy(session.scalar(select(SuggestionEvaluationModel)).comparisons)
            self.assertEqual(initial[0]["outcome"], "retained")
            self.assertEqual((initial[0]["suggested"], initial[0]["applied"]), (12, 10))
        with self.factory.begin() as session:
            row = session.scalar(select(EventLearningRecordModel).where(EventLearningRecordModel.event_id == "draft"))
            row.corrections = {"requirements": {"final_planned": [{"capability": "furniture.chair", "amount": 16}]}}
            row.version += 1
        self.service.evaluate("a", "a", "draft")
        with self.factory() as session:
            rows = session.scalars(select(SuggestionEvaluationModel).order_by(SuggestionEvaluationModel.learning_version)).all()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0].comparisons, initial)
            self.assertEqual(rows[1].comparisons[0]["outcome"], "increased")
        summary = self.service.summary("a", "a")
        self.assertEqual(summary["counts"]["applied_with_edits"], 1)
        self.assertEqual(summary["recent"][0]["comparisons"][0]["outcome"], "increased")

    def test_malformed_quantities_and_foreign_fields_do_not_record_outcome(self):
        identity = self.generate()["session_id"]
        for quantities in ({}, {"furniture.chair": True}, {"furniture.chair": 0}, {"furniture.chair": 10001}, {"unknown": 2}):
            with self.assertRaises(ValueError):
                self.action(identity, quantities=quantities)
        with self.assertRaises(ValueError):
            self.action(identity, organization_id="b")
        with self.factory() as session:
            self.assertIsNone(session.get(SuggestionSessionModel, identity).action)

    def test_provenance_is_part_of_the_immutable_explanation(self):
        result = self.generate(provenance={"duration_minutes": "defaulted"})
        self.assertEqual(result["provenance"]["duration_minutes"], "defaulted")
        self.assertTrue(any("planner default" in warning for warning in result["warnings"]))
        self.assertEqual(result["features"], FEATURES)
        with self.assertRaises(StateConflict):
            self.generate(provenance={"duration_minutes": "operator"})
        for provenance in ({"duration_minutes": []}, {"unknown": "operator"}, {"duration_minutes": "certain"}):
            with self.assertRaises(ValueError):
                self.generate(idempotency_key=uuid4().hex, provenance=provenance)

    def test_comparison_classification_matrix_and_empty_evidence(self):
        identity = self.generate()["session_id"]
        self.action(identity)
        with self.factory.begin() as session:
            event = session.get(EventModel, "draft")
            event.data = {"capability_requirements": [{"capability": "furniture.chair", "amount": 12}]}
            self.service.link_in_session(session, event, "a", identity)
            event.status = "returned"
            EventLearningStore.create_in_session(session, event)
        for index, (amount, has_return, expected) in enumerate(((12, False, "insufficient_evidence"), (12, True, "retained"), (16, True, "increased"), (10, True, "decreased"), (0, True, "removed")), 1):
            with self.factory.begin() as session:
                row = session.scalar(select(EventLearningRecordModel).where(EventLearningRecordModel.event_id == "draft"))
                row.version = index
                row.execution = {"returned": [[{"amount": 12}]] if has_return else []}
                row.corrections = {"requirements": {"final_planned": [{"capability": "furniture.chair", "amount": amount}] if amount else []}}
            self.service.evaluate("a", "a", "draft")
            with self.factory() as session:
                row = session.scalar(select(SuggestionEvaluationModel).where(SuggestionEvaluationModel.learning_version == index))
                self.assertEqual(row.comparisons[0]["outcome"], expected)
        self.assertEqual(self.service.summary("a", "a")["outcomes"], {"removed": 1})

    def test_foreign_link_rolls_back_creation_and_draft_deletion_preserves_snapshot(self):
        from database import TransactionalEventCreation, TransactionalEventOperations
        identity = self.generate()["session_id"]
        self.action(identity)
        creator = TransactionalEventCreation(self.factory)
        with self.factory() as session:
            before = session.scalar(select(func.count()).select_from(EventModel))
        with self.assertRaises(ResourceNotFound):
            creator.create("b", "b", "foreign-link", "request", {"suggestion_session_id": identity}, {"title": "Rejected"})
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(EventModel)), before)
            self.assertIsNone(session.get(SuggestionSessionModel, identity).event_id)
        event, _ = creator.create("a", "a", "own-link", "request", {"suggestion_session_id": identity}, {"title": "\u05d0\u05d9\u05e8\u05d5\u05e2 \u0639\u0631\u0628\u064a"})
        TransactionalEventOperations(self.factory).delete_draft("a", event.id, "a", "request")
        with self.factory() as session:
            row = session.get(SuggestionSessionModel, identity)
            self.assertIsNone(row.event_id)
            self.assertEqual(row.event_id_snapshot, event.id)
            self.assertEqual(row.event_title_snapshot, "\u05d0\u05d9\u05e8\u05d5\u05e2 \u0639\u0631\u0628\u064a")
            self.assertEqual(row.action, "applied")


class TestSuggestionOutcomesSQLite(OutcomeCases, unittest.TestCase):
    def setUp(self):
        self.engine, self.factory = sqlite_factory()
        self.addCleanup(self.engine.dispose)
        self.seed()


@postgres_required
class TestSuggestionOutcomesPostgres(OutcomeCases, unittest.TestCase):
    def setUp(self):
        url = os.environ["SALAMANDRA_TEST_POSTGRES_URL"]
        schema = "suggestion_outcomes_" + uuid4().hex
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
