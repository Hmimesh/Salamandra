from copy import deepcopy
from pathlib import Path
import os
import sys
import unittest
from uuid import uuid4

from sqlalchemy import create_engine, select, func, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from acceptance_support import postgres_required
from database import (Base, OrganizationModel, UserModel, MembershipModel,
    EventProposalModel, EventModel, EventLearningRecordModel, TransactionalEventCreation)
from event_proposals import capture_proposal
from security import ResourceNotFound, StateConflict
from test_database_phase2 import sqlite_factory


class ProposalCases:
    def seed(self):
        with self.factory.begin() as session:
            for name in ("a", "b"):
                session.add(OrganizationModel(id=name, name=name))
                session.add(UserModel(id=name, name=name, email=f"{name}@example.test", password_hash="unused"))
                session.flush()
                session.add(MembershipModel(id=name, organization_id=name, user_id=name, role="owner"))
        self.data = {"title": "\u05d0\u05d9\u05e8\u05d5\u05e2", "description": "\u0639\u0631\u0628\u064a", "capability_requirements": [{"capability": "monitor.stage", "amount": 2}]}
        self.identity = capture_proposal(self.factory, "a", "a", self.data, {"description": self.data["description"]})["proposal_id"]
        self.creator = TransactionalEventCreation(self.factory)

    def test_original_preview_survives_correction_and_replay(self):
        corrected = deepcopy(self.data)
        corrected["capability_requirements"][0]["amount"] = 4
        payload = {"proposal_id": self.identity}
        event, created = self.creator.create("a", "a", "save", "request", payload, corrected)
        self.assertTrue(created)
        repeated, created = self.creator.create("a", "a", "save", "retry", payload, corrected)
        self.assertFalse(created)
        self.assertEqual(event.id, repeated.id)
        with self.factory() as session:
            row = session.scalar(select(EventLearningRecordModel).where(EventLearningRecordModel.event_id == event.id))
            self.assertEqual(row.proposal["requirements"][0]["amount"], 2)
            self.assertEqual(row.corrections["requirements"]["final_planned"][0]["amount"], 4)
            self.assertEqual(row.original_request["brief"], self.data["description"])
        with self.assertRaises(StateConflict):
            self.creator.create("a", "a", "second-event", "request", payload, corrected)
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(EventModel)), 1)

    def test_capture_retry_preserves_displayed_snapshot_and_rejects_changed_request(self):
        first = capture_proposal(self.factory, "a", "a", self.data, {"brief": "same"}, "retry")
        changed = {**self.data, "title": "New computation"}
        replay = capture_proposal(self.factory, "a", "a", changed, {"brief": "same"}, "retry")
        self.assertEqual(first, replay)
        with self.assertRaises(StateConflict):
            capture_proposal(self.factory, "a", "a", changed, {"brief": "different"}, "retry")

    def test_foreign_and_missing_proposals_rollback_creation(self):
        for identity in (self.identity, "absent"):
            with self.assertRaises(ResourceNotFound):
                self.creator.create("b", "b", identity, "request", {"proposal_id": identity}, self.data)
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(EventModel)), 0)
            self.assertFalse(session.get(EventProposalModel, self.identity).consumed)


class TestProposalsSQLite(ProposalCases, unittest.TestCase):
    def setUp(self):
        self.engine, self.factory = sqlite_factory()
        self.addCleanup(self.engine.dispose)
        self.seed()


@postgres_required
class TestProposalsPostgres(ProposalCases, unittest.TestCase):
    def setUp(self):
        url = os.environ["SALAMANDRA_TEST_POSTGRES_URL"]
        schema = "proposals_" + uuid4().hex
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
