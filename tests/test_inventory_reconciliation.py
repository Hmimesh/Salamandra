from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sqlalchemy import select, func
from catalog_terms import index_workspace_terms
from database import Base, CatalogTermModel, CatalogDecisionModel, InventoryHoldingModel, InventoryAdjustmentModel
from inventory_reconciliation import parse_csv, review, DuplicateIndex
from postgres_reconciliation import PostgresReconciliation
from postgres_runtime import PostgresRuntime
from Item_node import ItemNode
from security import AccessDenied, ResourceNotFound, StateConflict
try:
    from tests.test_database_phase2 import sqlite_factory
except ModuleNotFoundError:
    from test_database_phase2 import sqlite_factory


class TestCsvReview(unittest.TestCase):
    def test_multilingual_headers_and_grouped_categories(self):
        result = review({"csv": "שם,כמות,סוג\nרמקול,2,רמקולים\nעוד רמקול,1,רמקולים\nميكروفون,1,ميكروفونات\nCafé 🎤,2,cable"}, {}, [])
        self.assertEqual(result["suggested_mapping"]["name"], "שם")
        self.assertEqual(result["categories"][0]["count"], 2)
        self.assertEqual(result["rows"][0]["canonical_type"], "speaker")
        self.assertEqual(result["rows"][2]["canonical_type"], "microphone")
        self.assertEqual(result["counts"]["create"], 4)

    def test_duplicates_preserve_distinct_model_numbers(self):
        index = DuplicateIndex()
        index.add({"target": "a", "name": "EV ZLX-15P", "model": "ZLX-15P", "manufacturer": "Electro-Voice", "canonical_type": "speaker"})
        self.assertEqual(index.matches({"name": "Electro Voice ZLX 15P", "canonical_type": "speaker"})[0]["match"], "likely")
        self.assertEqual(index.matches({"name": "EV ZLX-12P", "model": "ZLX-12P", "canonical_type": "speaker"}), [])
        self.assertEqual(index.matches({"name": "EV ZLX15P", "canonical_type": "microphone"}), [])

    def test_invalid_rows_must_be_corrected_or_skipped(self):
        body = {"csv": "name,count,type\nChair,wrong,furniture\nTable,1,furniture,extra"}
        self.assertEqual(review(body, {}, [])["counts"]["unresolved"], 2)
        body["decisions"] = {"1": {"action": "skip"}, "2": {"action": "skip"}}
        self.assertEqual(review(body, {}, [])["counts"]["skip"], 2)

    def test_malformed_and_oversized_csv_fail_before_review(self):
        for value in ('name,count\n"bad,2', "name,name\nA,B", "name,count\nA,1\x00", "name,count\n" + "A,1\n" * 10001, "a" * 2_000_001):
            with self.subTest(value=value[:30]), self.assertRaises(ValueError):
                parse_csv({"csv": value})

    def test_unknown_and_forged_category_require_review(self):
        body = {"csv": "name,count,type\nCustom,2,חדש"}
        self.assertEqual(review(body, {}, [])["counts"]["unresolved"], 1)
        body["category_choices"] = {"חדש": {"action": "map", "code": "custom_foreign"}}
        with self.assertRaises(ValueError):
            review(body, {}, [])

    def test_duplicate_chains_have_bounded_flat_evidence(self):
        import json
        body = {"csv": "name,count,type\n" + "SM58,1,microphone\n" * 1000,
                "decisions": {str(row): {"action": "new"} for row in range(1, 1001)}}
        result = review(body, {}, [])
        self.assertLess(len(json.dumps(result)), 2_000_000)
        self.assertTrue(all("candidates" not in candidate for row in result["rows"] for candidate in row["candidates"]))


class TestReviewedImportTransaction(unittest.TestCase):
    def setUp(self):
        self.engine, self.factory = sqlite_factory()
        self.runtime = PostgresRuntime(self.factory)
        self.owner = self.runtime.accounts.create_user("Owner", "a@example.test", "Test-password-123", "owner", "a", "A")
        self.other = self.runtime.accounts.create_user("Other", "b@example.test", "Test-password-123", "owner", "b", "B")
        self.operator = self.runtime.accounts.create_user("Operator", "op@example.test", "Test-password-123", "operator", "a", "A")
        self.service = self.runtime.reconciliation
        self.runtime.workspace.add_item(ItemNode(id="ev15", type="pa", display_name="EV ZLX-15P", manufacturer="Electro-Voice", model="ZLX-15P"), 4, "shared", self.owner.id, "a")
        self.body = {"csv": "name,count,type\nElectro Voice ZLX 15P,2,speaker", "idempotency_key": "import-one"}

    def tearDown(self):
        self.engine.dispose()

    def snapshot(self):
        with self.engine.connect() as connection:
            return {table.name: sorted([repr(dict(row._mapping)) for row in connection.execute(table.select())]) for table in Base.metadata.sorted_tables}

    def choose_add(self):
        candidate = self.service.preview("a", self.owner.id, self.body)["rows"][0]["candidates"][0]
        self.body["decisions"] = {"1": {"action": "add", "target": candidate["target"], "expected": candidate["expected"]}}
        return candidate

    def test_additive_import_retry_preserves_definition_and_ledger(self):
        self.choose_add()
        result = self.service.commit("a", self.owner.id, self.body, "request")
        before = self.snapshot()
        self.assertEqual(self.service.commit("a", self.owner.id, self.body, "retry"), result)
        self.assertEqual(self.snapshot(), before)
        with self.factory() as session:
            holding = session.scalar(select(InventoryHoldingModel).where(InventoryHoldingModel.organization_id == "a"))
            self.assertEqual(holding.available_quantity, 6)
            self.assertEqual(holding.data["display_name"], "EV ZLX-15P")
            self.assertEqual(session.scalar(select(func.count()).select_from(InventoryAdjustmentModel)), 2)
        with self.assertRaises(StateConflict):
            self.service.commit("a", self.owner.id, {**self.body, "csv": self.body["csv"].replace(",2,", ",3,")}, "conflict")

    def test_catalog_stock_and_history_roll_back_together(self):
        body = {"csv": "name,count,type\nNew,2,חדש", "idempotency_key": "all-or-none",
                "category_choices": {"חדש": {"action": "custom", "label": "אירוח خاص", "remember": True}}}
        before = self.snapshot()
        with patch.object(self.service.stock, "_record_adjustment", side_effect=StateConflict("injected ledger failure")):
            with self.assertRaises(StateConflict):
                self.service.commit("a", self.owner.id, body, "fail")
        self.assertEqual(self.snapshot(), before)
        self.service.commit("a", self.owner.id, body, "success")
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(CatalogTermModel)), 2)

    def test_lower_role_can_import_but_not_learn_aliases(self):
        self.choose_add()
        self.service.commit("a", self.operator.id, self.body, "operator")
        before = self.snapshot()
        for choice in ({"action": "custom", "label": "New category"}, {"action": "map", "code": "speaker", "remember": True}):
            with self.assertRaises(AccessDenied):
                self.service.commit("a", self.operator.id, {"csv": "name,count,type\nNew,1,חדש", "idempotency_key": "no", "category_choices": {"חדש": choice}}, "deny")
            self.assertEqual(self.snapshot(), before)

    def test_alias_failure_rolls_back_earlier_category_creation(self):
        self.runtime.canonical_catalog.create("a", self.owner.id, "alias", {
            "label": "Known label", "canonical_type": "microphone", "idempotency_key": "known",
        }, "fixture")
        body = {"csv": "name,count,type\nNew,1,Fresh label\nSecond,1,Known label", "idempotency_key": "alias-conflict",
                "category_choices": {"Fresh label": {"action": "custom", "label": "New custom category"},
                                     "Known label": {"action": "map", "code": "speaker", "remember": True}}}
        before = self.snapshot()
        with self.assertRaises(StateConflict):
            self.service.commit("a", self.owner.id, body, "rollback")
        self.assertEqual(self.snapshot(), before)

    def test_product_review_is_scoped_and_stale_safe(self):
        self.runtime.workspace.add_item(ItemNode(id="second-ev", type="pa", display_name="Electro Voice ZLX15P", model="ZLX-15P"), 1, "shared", self.owner.id, "a")
        pair = self.service.cleanup("a", self.owner.id)["pairs"][0]
        decision = {"left": pair["left"]["target"], "right": pair["right"]["target"], "pair_key": pair["pair_key"], "action": "same"}
        before = self.snapshot()
        with self.assertRaises(ResourceNotFound):
            self.service.decide("b", self.other.id, decision, "foreign")
        with self.assertRaises(AccessDenied):
            self.service.decide("a", self.operator.id, decision, "role")
        with self.assertRaises(StateConflict):
            self.service.decide("a", self.owner.id, {**decision, "pair_key": "stale"}, "stale")
        self.assertEqual(self.snapshot(), before)
        self.service.decide("a", self.owner.id, decision, "same")
        self.assertEqual(self.service.cleanup("a", self.owner.id)["reviewed_pairs"], 1)
        self.assertEqual(self.service.cleanup("b", self.other.id)["reviewed_pairs"], 0)
        result = self.service.preview("a", self.owner.id, self.body)
        self.assertEqual(result["rows"][0]["action"], "review")
        self.assertEqual(len(result["rows"][0]["candidates"]), 2)

    def test_foreign_stale_archived_targets_and_stock_changes(self):
        candidate = self.choose_add()
        with self.assertRaises(ResourceNotFound):
            self.service.commit("b", self.other.id, self.body, "foreign")
        with self.factory.begin() as session:
            holding = session.get(InventoryHoldingModel, candidate["target"])
            holding.data = {**holding.data, "model": "Changed model"}
        with self.assertRaises(StateConflict):
            self.service.commit("a", self.owner.id, self.body, "stale")
        with self.factory.begin() as session:
            session.get(InventoryHoldingModel, candidate["target"]).active = False
        with self.assertRaises(ResourceNotFound):
            self.service.commit("a", self.owner.id, self.body, "archived")

    def test_keep_separate_and_in_file_addition_do_not_merge_history(self):
        self.body["decisions"] = {"1": {"action": "new"}, "2": {"action": "add", "target": "row:1"}}
        self.body["csv"] += "\nEV ZLX15P,3,speaker"
        self.service.commit("a", self.owner.id, self.body, "separate")
        with self.factory() as session:
            rows = list(session.scalars(select(InventoryHoldingModel)))
            self.assertEqual(sorted(row.available_quantity for row in rows), [4, 5])
        cleanup = self.service.cleanup("a", self.owner.id)
        pair = cleanup["pairs"][0]
        body = {"left": pair["left"]["target"], "right": pair["right"]["target"], "pair_key": pair["pair_key"], "action": "separate"}
        self.service.decide("a", self.owner.id, body, "decision")
        before = self.snapshot()
        self.service.decide("a", self.owner.id, body, "retry")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.service.cleanup("a", self.owner.id)["counts"]["duplicates"], 0)
        with self.assertRaises(AccessDenied):
            self.service.cleanup("a", self.operator.id)
