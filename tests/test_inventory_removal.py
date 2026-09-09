import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sqlalchemy import select
from database import Base, InventoryHoldingModel, InventoryAdjustmentModel, AuditEventModel
from postgres_runtime import PostgresRuntime
from Item_node import ItemNode
from security import AccessDenied, StateConflict, ResourceNotFound
try:
    from tests.test_database_phase2 import sqlite_factory
except ModuleNotFoundError:
    from test_database_phase2 import sqlite_factory


class TestInventoryRemoval(unittest.TestCase):
    def setUp(self):
        self.engine, self.factory = sqlite_factory()
        self.runtime = PostgresRuntime(self.factory)
        self.owner = self.runtime.accounts.create_user("Owner", "owner@example.test", "Test-password-123", "owner", "a", "A")
        self.other = self.runtime.accounts.create_user("Other", "other@example.test", "Test-password-123", "owner", "b", "B")
        self.service = self.runtime.removals
        self.runtime.workspace.add_item(ItemNode(id="chair", type="other"), 4, "shared", self.owner.id, "a")

    def tearDown(self):
        self.engine.dispose()

    def snapshot(self):
        with self.engine.connect() as connection:
            return {table.name: sorted(repr(dict(row._mapping)) for row in connection.execute(table.select())) for table in Base.metadata.sorted_tables}

    def body(self):
        return {"confirmation": "CLEAR INVENTORY", "idempotency_key": "clear-one", "preview_token": self.service.preview("a", self.owner.id)["preview_token"]}

    def test_owner_clear_audited_and_retry_is_read_only(self):
        self.runtime.workspace.add_item(ItemNode(id="private", type="other"), 2, "personal", self.owner.id, "a")
        self.runtime.workspace.add_item(ItemNode(id="foreign", type="other"), 3, "shared", self.other.id, "b")
        body = self.body()
        result = self.service.execute("a", self.owner.id, body, "request", clear=True)
        self.assertEqual((result["archived"], result["units_removed"]), (1, 4))
        before = self.snapshot()
        self.assertEqual(self.service.execute("a", self.owner.id, body, "retry", clear=True), result)
        self.assertEqual(self.snapshot(), before)
        with self.factory() as session:
            rows = {row.legacy_item_id: row for row in session.scalars(select(InventoryHoldingModel))}
            self.assertFalse(rows["chair"].active)
            self.assertEqual(rows["chair"].available_quantity, 0)
            self.assertEqual(rows["private"].available_quantity, 2)
            self.assertEqual(rows["foreign"].available_quantity, 3)
            adjustments = list(session.scalars(select(InventoryAdjustmentModel).where(InventoryAdjustmentModel.operation == "inventory.clear")))
            self.assertEqual(len(adjustments), 1)
            self.assertEqual(adjustments[0].delta, -4)
            self.assertIsNotNone(session.scalar(select(AuditEventModel).where(AuditEventModel.action == "inventory.clear")))
        with self.assertRaises(StateConflict):
            self.service.execute("a", self.owner.id, {**body, "preview_token": "different"}, "conflict", clear=True)
        self.assertEqual(self.snapshot(), before)

    def test_only_owner_even_with_forged_role(self):
        body = self.body()
        for role in ("admin", "operator", "producer", "technician", "read_only"):
            user = self.runtime.accounts.create_user(role, f"{role}@example.test", "Test-password-123", role, "a", "A")
            before = self.snapshot()
            with self.subTest(role=role), self.assertRaises(AccessDenied):
                self.service.execute("a", user.id, {**body, "role": "owner", "actor": self.owner.id}, "forged", clear=True)
            self.assertEqual(self.snapshot(), before)

    def test_each_operational_bucket_blocks_atomically(self):
        for bucket in ("reserved_quantity", "packed_quantity", "dispatched_quantity"):
            with self.factory.begin() as session:
                row = session.scalar(select(InventoryHoldingModel))
                row.available_quantity = 3
                setattr(row, bucket, 1)
            body = self.body()
            self.assertTrue(self.service.preview("a", self.owner.id)["blocked"])
            before = self.snapshot()
            with self.subTest(bucket=bucket), self.assertRaises(StateConflict):
                self.service.execute("a", self.owner.id, body, "blocked", clear=True)
            self.assertEqual(self.snapshot(), before)
            with self.factory.begin() as session:
                setattr(session.scalar(select(InventoryHoldingModel)), bucket, 0)

    def test_shared_dependency_graph_archives_together(self):
        self.runtime.workspace.add_item(ItemNode.from_dict({"id": "table", "type": "other", "requirements": [{"item_id": "chair", "amount": 1}]}), 1, "shared", self.owner.id, "a")
        result = self.service.execute("a", self.owner.id, self.body(), "graph", clear=True)
        self.assertEqual(result["archived"], 2)
        with self.factory() as session:
            rows = list(session.scalars(select(InventoryHoldingModel)))
            self.assertTrue(all(not row.active for row in rows))
            self.assertTrue(next(row for row in rows if row.legacy_item_id == "table").data["requirements"])

    def test_personal_dependency_blocks_entire_clear(self):
        self.runtime.workspace.add_item(ItemNode.from_dict({"id": "private", "type": "other", "requirements": [{"item_id": "chair", "amount": 1}]}), 1, "personal", self.owner.id, "a")
        body, before = self.body(), self.snapshot()
        with self.assertRaises(StateConflict):
            self.service.execute("a", self.owner.id, body, "graph", clear=True)
        self.assertEqual(self.snapshot(), before)

    def test_failure_after_mutation_rolls_back_ledger_audit_and_receipt(self):
        body, before = self.body(), self.snapshot()
        with patch.object(self.service, "_audit_definition", side_effect=StateConflict("injected after ledger write")):
            with self.assertRaises(StateConflict):
                self.service.execute("a", self.owner.id, body, "failure", clear=True)
        self.assertEqual(self.snapshot(), before)
        self.service.execute("a", self.owner.id, body, "retry", clear=True)

    def test_confirmation_stale_preview_and_foreign_actor_do_not_write(self):
        body, before = self.body(), self.snapshot()
        for changes, error in (({"confirmation": "clear"}, ValueError), ({"preview_token": "stale"}, StateConflict)):
            with self.assertRaises(error):
                self.service.execute("a", self.owner.id, {**body, **changes}, "invalid", clear=True)
        with self.assertRaises(ResourceNotFound):
            self.service.execute("a", self.other.id, body, "foreign", clear=True)
        self.assertEqual(self.snapshot(), before)

    def test_zero_archive_preserves_record_and_is_idempotent(self):
        body = {"item_id": "chair", "scope": "shared", "idempotency_key": "archive"}
        with self.assertRaises(StateConflict):
            self.service.execute("a", self.owner.id, body, "nonempty")
        with self.factory.begin() as session:
            session.scalar(select(InventoryHoldingModel)).available_quantity = 0
        result = self.service.execute("a", self.owner.id, body, "empty")
        before = self.snapshot()
        self.assertEqual(self.service.execute("a", self.owner.id, body, "retry"), result)
        self.assertEqual(self.snapshot(), before)

    def test_personal_archive_receipt_cannot_be_reused_by_another_owner(self):
        tech = self.runtime.accounts.create_user("Tech", "tech@example.test", "Test-password-123", "technician", "a", "A")
        for user in (self.owner, tech):
            self.runtime.workspace.add_item(ItemNode(id="private", type="other"), 1, "personal", user.id, "a")
        with self.factory.begin() as session:
            for holding in session.scalars(select(InventoryHoldingModel).where(InventoryHoldingModel.scope == "personal")):
                holding.available_quantity = 0
        body = {"item_id": "private", "scope": "personal", "idempotency_key": "same-personal-key"}
        self.service.execute("a", self.owner.id, body, "first")
        before = self.snapshot()
        with self.assertRaises(StateConflict):
            self.service.execute("a", tech.id, body, "second")
        self.assertEqual(self.snapshot(), before)
