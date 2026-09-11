from copy import deepcopy
import sys
from pathlib import Path
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from event_similarity import EventSimilarityService, MAX_CANDIDATE_EXAMPLES, score_features, validate_features

FEATURES = {"departments": ["furniture"], "guest_count": 100, "duration_minutes": 120, "venue_type": "indoor"}


def example(identity="a", amount=12, feedback=None):
    return {"event_id": identity, "features": deepcopy(FEATURES),
            "proposal": {"requirements": [{"capability": "furniture.chair", "amount": amount}], "lines": [{"item_id": "כיסא عربي 🎛️", "capability": "furniture.chair", "amount": amount}]},
            "corrections": {}, "feedback": feedback}


class TestEventSimilarity(unittest.TestCase):
    def service(self, *examples):
        self.store = Mock()
        self.store.examples.return_value = list(examples)
        return EventSimilarityService(self.store)

    def test_exact_match_stable_order_and_bound(self):
        service = self.service(example("b"), example("a"))
        first = service.rank("org-a", FEATURES)
        self.assertEqual(first, service.rank("org-a", FEATURES))
        self.assertEqual([m["event_id"] for m in first], ["a", "b"])
        self.assertEqual(first[0]["score"], 100)
        self.store.examples.assert_called_with("org-a", MAX_CANDIDATE_EXAMPLES)

    def test_different_and_missing_features_do_not_match_strongly(self):
        service = self.service(example())
        for features in ({}, {"venue_type": "indoor"}, {"departments": ["audio"], "guest_count": 10000, "duration_minutes": 10000, "venue_type": "outdoor"}):
            self.assertEqual(service.rank("org-a", features), [])
        self.assertLess(score_features(FEATURES, {"venue_type": "indoor"})[0], 60)

    def test_multiple_examples_aggregate_and_one_does_not(self):
        self.assertEqual(self.service(example()).suggestions("a", FEATURES)["suggestions"], [])
        result = self.service(example("a"), example("b"), example("c", 14)).suggestions("a", FEATURES)
        self.assertEqual(result["suggestions"][0]["amount"], 12)
        self.assertEqual(result["suggestions"][0]["evidence_count"], 3)
        self.assertEqual(result["suggestions"][0]["maximum"], 14)
        self.assertEqual(self.service(example("a"), example("b", 40)).suggestions("a", FEATURES)["suggestions"], [])

    def test_feedback_rejected_or_excess_plan_is_not_copied(self):
        for feedback in ({"reuse_plan": "no"}, {"plan_fit": "too_much"}, {"plan_fit": "too_little"}, {"reuse_plan": "with_changes"}):
            result = self.service(example("a", feedback=feedback), example("b")).suggestions("a", FEATURES)
            self.assertEqual(result["suggestions"], [])
            self.assertTrue(result["warnings"])

    def test_corrected_requirements_used_for_small_plan(self):
        a, b = example("a", 2, {"plan_fit": "too_little"}), example("b", 3)
        a["corrections"] = {"changed": True, "requirements": {"final_planned": [{"capability": "furniture.chair", "amount": 3}]}}
        self.assertEqual(self.service(a, b).suggestions("a", FEATURES)["suggestions"][0]["amount"], 3)

    def test_item_feedback_blocks_unsafe_quantity_and_preserves_unicode(self):
        for kind in ("missing", "unnecessary", "additional_onsite", "failed"):
            a = example("a", feedback={kind: "yes", "notes": "פרטי خاص", "items": [{"kind": kind, "item_id": "כיסא عربي 🎛️", "quantity": 1}]})
            original = deepcopy(a)
            result = self.service(a, example("b")).suggestions("org-a", FEATURES)
            self.assertEqual(result["suggestions"], [])
            self.assertEqual(a, original)
            self.assertNotIn("פרטי", str(result))
            self.assertNotIn("event_id", str(result))

    def test_current_event_cannot_contribute(self):
        self.assertEqual(self.service(example("current"), example("b")).suggestions("org-a", FEATURES, "current")["suggestions"], [])

    def test_unknown_feedback_mapping_and_uncorrected_quantities_are_not_reused(self):
        a = example("a", feedback={"items": [{"kind": "missing", "item_id": "not-in-plan"}]})
        self.assertEqual(self.service(a, example("b")).suggestions("a", FEATURES)["suggestions"], [])
        a = example("a", feedback={"plan_fit": "too_little"})
        a["corrections"] = {"changed": True, "requirements": {"final_planned": deepcopy(a["proposal"]["requirements"])}}
        self.assertEqual(self.service(a, example("b")).suggestions("a", FEATURES)["suggestions"], [])

    def test_corrected_reuse_and_priority_breakdown_use_final_total(self):
        a = example("a", 10, {"reuse_plan": "with_changes"})
        a["corrections"] = {"changed": True, "requirements": {"final_planned": [
            {"capability": "furniture.chair", "amount": 10, "level": "required"},
            {"capability": "furniture.chair", "amount": 2, "level": "recommended"}]}}
        result = self.service(a, example("b", 12)).suggestions("a", FEATURES)
        self.assertEqual(result["suggestions"][0]["amount"], 12)

    def test_unsupported_malformed_oversized_features_rejected(self):
        for value in (None, [], {"description": "raw"}, {"guest_count": True}, {"duration_minutes": float("nan")}, {"guest_count": 1000001}, {"departments": ["x"] * 33}, {"venue_type": "x" * 81}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_features(value)

    def test_no_new_departments_and_no_mutation(self):
        a = example()
        a["proposal"]["requirements"].append({"capability": "lighting.fixture", "amount": 2})
        original = deepcopy(a)
        result = self.service(a, {**deepcopy(a), "event_id": "b"}).suggestions("org-a", FEATURES)
        self.assertEqual([s["capability"] for s in result["suggestions"]], ["furniture.chair"])
        self.assertEqual(original, a)

    def test_similarity_sanity_matrix_keeps_fixed_weights(self):
        matrix = [
            (FEATURES, 100),
            ({**FEATURES, "guest_count": 1000}, 73),
            ({**FEATURES, "departments": ["video"]}, 65),
            ({"venue_type": "indoor"}, 20),
            ({}, 0),
            ({"departments": ["video"], "guest_count": 10000, "duration_minutes": 1200, "venue_type": "outdoor"}, 1.8),
        ]
        for historical, expected in matrix:
            with self.subTest(historical=historical):
                score, reasons = score_features(FEATURES, historical)
                self.assertEqual(score, expected)
                self.assertTrue(all(reason["similarity"] > 0 for reason in reasons))

    def test_quantity_matrix_missing_noisy_corrected_and_two_examples(self):
        for amounts, expected in (((12, 12, 14), 12), ((12, 30, 3), None), ((12,), None),
                                  ((12, 12), 12), ((12, None), None), ((12, 0, 12), 12)):
            with self.subTest(amounts=amounts):
                result = self.service(*(example(str(i), amount) for i, amount in enumerate(amounts))).suggestions("a", FEATURES)
                self.assertEqual(result["suggestions"][0]["amount"] if result["suggestions"] else None, expected)
        a, b = example("a"), example("b")
        b["corrections"] = {"changed": True, "requirements": {"final_planned": [{"capability": "furniture.chair", "amount": 30}]}}
        self.assertEqual(self.service(a, b).suggestions("a", FEATURES)["suggestions"], [])

    def test_repeated_onsite_issues_are_counted_not_added_to_planned_quantity(self):
        review = {"additional_onsite": "yes", "items": [{"kind": "additional_onsite", "item_id": "כיסא عربي 🎛️", "quantity": 2}]}
        result = self.service(example("a", feedback=review), example("b", feedback=review)).suggestions("a", FEATURES)
        self.assertEqual(result["suggestions"], [])
        self.assertTrue(any("2 similar events" in warning and "additional onsite" in warning and "furniture.chair" in warning for warning in result["warnings"]))

    def test_failed_equipment_without_identified_item_is_not_preferred(self):
        result = self.service(example("a", feedback={"failed": "yes"}), example("b")).suggestions("a", FEATURES)
        self.assertEqual(result["suggestions"], [])
