from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from uuid import uuid4
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from database import (
    AllocationModel,
    AuditEventModel,
    Base,
    EventModel,
    InventoryHoldingModel,
    MembershipModel,
    OrganizationModel,
    StockMovementModel,
    TransactionalEventOperations,
    UserModel,
)
from migrate_json_to_postgres import migrate_json
from security import ResourceNotFound, StateConflict


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

    @unittest.skipUnless(
        os.environ.get("SALAMANDRA_TEST_POSTGRES_URL"),
        "Requires an isolated PostgreSQL test database for row-lock concurrency proof.",
    )
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
    def test_initial_migration_upgrades_and_downgrades_clean_database(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "migration.sqlite3"
            config = Config(str(ROOT / "alembic.ini"))
            config.set_main_option("script_location", str(ROOT / "migrations"))
            config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")

            command.upgrade(config, "head")
            engine = create_engine(f"sqlite:///{database_path.as_posix()}")
            upgraded_tables = set(sqlalchemy_inspect(engine).get_table_names())
            engine.dispose()

            command.downgrade(config, "base")
            engine = create_engine(f"sqlite:///{database_path.as_posix()}")
            downgraded_tables = set(sqlalchemy_inspect(engine).get_table_names())
            engine.dispose()

        self.assertIn("inventory_holdings", upgraded_tables)
        self.assertIn("stock_movements", upgraded_tables)
        self.assertEqual(downgraded_tables, {"alembic_version"})


if __name__ == "__main__":
    unittest.main()
