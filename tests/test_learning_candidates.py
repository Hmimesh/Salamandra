"""Phase E candidate retrieval, including isolated real PostgreSQL coverage."""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import unittest
from uuid import uuid4

from sqlalchemy import create_engine, event as sql_event, select, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from acceptance_support import postgres_required
from database import Base, EventLearningRecordModel, EventModel, MembershipModel, OrganizationModel, UserModel
from event_learning import EventLearningStore
from event_similarity import EventSimilarityService
from test_database_phase2 import sqlite_factory

FEATURES = {"departments": ["furniture"], "guest_count": 100, "duration_minutes": 120, "venue_type": "indoor"}


class CandidateCases:
    def seed(self):
        self.store = EventLearningStore(self.factory)
        with self.factory.begin() as session:
            for org in ("candidate-a", "candidate-b"):
                session.add(OrganizationModel(id=org, name=org))
                session.add(UserModel(id=org, email=f"{org}@example.test", name=org, password_hash="unused"))
                session.flush()
                session.add(MembershipModel(id=org, organization_id=org, user_id=org, role="owner"))
            session.flush()
            for index in range(305):
                org = "candidate-b" if index >= 303 else "candidate-a"
                relevant = index >= 300
                data = {"attendee_count": 100 if relevant else 10000,
                        "duration_minutes": 120 if relevant else 900,
                        "venue_kind": "indoor" if relevant else "outdoor",
                        "capability_requirements": [{"capability": "furniture.chair" if relevant else "lighting.fixture", "amount": 12}],
                        "description": "\u05e2\u05d1\u05e8\u05d9\u05ea \u0639\u0631\u0628\u064a"}
                event = EventModel(id=f"candidate-{index:03}", organization_id=org, owner_user_id=org,
                                   title="Candidate", status="returned", data=data)
                session.add(event)
                session.flush()
                row = self.store.create_in_session(session, event)
                row.eligible = True
                row.exclusion_reason = None
                # Relevant history is older, and outside the old ID-first sample.
                row.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=0 if relevant else index)

    def test_large_history_retrieves_old_relevant_examples_in_one_statement(self):
        old = self.store.examples("candidate-a", 100)
        self.assertFalse(any(row["event_id"] == "candidate-300" for row in old))
        statements = []
        def capture(connection, cursor, statement, parameters, context, many):
            statements.append(statement)
        sql_event.listen(self.engine, "before_cursor_execute", capture)
        try:
            candidates = self.store.examples("candidate-a", 100, features=FEATURES)
        finally:
            sql_event.remove(self.engine, "before_cursor_execute", capture)
        self.assertEqual(len(statements), 1)
        self.assertEqual(len(candidates), 100)
        ids = {row["event_id"] for row in candidates}
        self.assertTrue({"candidate-300", "candidate-301", "candidate-302"} <= ids)
        self.assertNotIn("candidate-303", ids)
        self.assertIn("LIMIT", statements[0].upper())
        self.assertEqual(candidates, self.store.examples("candidate-a", 100, features=FEATURES))
        ranking = EventSimilarityService(self.store).rank("candidate-a", FEATURES)
        self.assertEqual([match["event_id"] for match in ranking], ["candidate-300", "candidate-301", "candidate-302"])
        self.assertEqual(ranking[0]["score"], 100)

    def test_sparse_features_use_recent_fallback_without_a_strong_match(self):
        candidates = self.store.examples("candidate-a", 2, features={})
        self.assertEqual({row["event_id"] for row in candidates}, {"candidate-298", "candidate-299"})
        self.assertEqual(EventSimilarityService(self.store).rank("candidate-a", {}), [])
        self.assertEqual(len(self.store.examples("candidate-a", 10000, features=FEATURES)), 100)
        for limit in (True, "100", None):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.store.examples("candidate-a", limit, features=FEATURES)

    def test_candidate_pools_preserve_every_eligibility_boundary(self):
        with self.factory.begin() as session:
            row = session.scalar(select(EventLearningRecordModel).where(EventLearningRecordModel.event_id == "candidate-300"))
            row.exclusion_reason = "owner_excluded"
            row = session.scalar(select(EventLearningRecordModel).where(EventLearningRecordModel.event_id == "candidate-301"))
            row.source_type = "synthetic"
            session.get(EventModel, "candidate-302").status = "out"
        selected = self.store.examples("candidate-a", 100, features=FEATURES)
        self.assertFalse({"candidate-300", "candidate-301", "candidate-302", "candidate-303"} & {row["event_id"] for row in selected})
        self.assertEqual(EventSimilarityService(self.store).suggestions("candidate-a", FEATURES)["suggestions"], [])
        self.assertEqual(EventSimilarityService(self.store).suggestions("candidate-b", FEATURES)["evidence_count"], 2)
        self.assertEqual(self.store.examples("unknown-org", 100, features=FEATURES), [])


class TestLearningCandidatesSQLite(CandidateCases, unittest.TestCase):
    def setUp(self):
        self.engine, self.factory = sqlite_factory()
        self.addCleanup(self.engine.dispose)
        self.seed()


@postgres_required
class TestLearningCandidatesPostgres(CandidateCases, unittest.TestCase):
    def setUp(self):
        url = os.environ["SALAMANDRA_TEST_POSTGRES_URL"]
        schema = f"learning_candidates_{uuid4().hex}"
        admin = create_engine(url, isolation_level="AUTOCOMMIT")
        self.addCleanup(admin.dispose)
        with admin.connect() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        def drop_schema():
            with admin.connect() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        self.addCleanup(drop_schema)
        self.engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.seed()
