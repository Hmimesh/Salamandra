from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from catalog_terms import normalize_match, resolve_category
from Item_node import ItemNode


class TestMultilingualCanonicalData(unittest.TestCase):
    def test_display_text_round_trip_is_lossless(self):
        for name in ("רמקול ראשי במה", "ميكروفون رئيسي", "Éclairage für Bühne", "במה Stage A 🎤"):
            with self.subTest(name=name):
                item = ItemNode(id="stable-id", type="pa", display_name=name, info=name)
                restored = ItemNode.from_dict(item.to_dict())
                self.assertEqual((restored.display_name, restored.info), (name, name))
                self.assertEqual(restored.canonical_type, "speaker")

    def test_normalization_is_matching_only(self):
        self.assertEqual(normalize_match(" ＥＶ  ZLX–15 "), normalize_match("ev zlx-15"))
        self.assertEqual(normalize_match("Cafe\u0301"), normalize_match("CAFÉ"))
        self.assertEqual(normalize_match("Straße"), normalize_match("STRASSE"))
        self.assertNotEqual(normalize_match("café"), normalize_match("cafe"))

    def test_builtin_aliases_are_canonical_and_unknowns_need_review(self):
        for label, code in (
            ("רמקולים", "speaker"), ("מיקרופונים", "microphone"), ("כבלים", "cable"),
            ("ميكروفونات", "microphone"), ("كابلات", "cable"),
            ("מיקרופון", "microphone"), ("micrófonos", "microphone"),
            ("câble", "cable"), ("Lautsprecher", "speaker"),
        ):
            result = resolve_category(label)
            self.assertEqual(result["canonical_type"], code)
            self.assertEqual(result["input_label"], label)
            self.assertEqual(result["source"], "system")
            self.assertFalse(result["confirmed_by_user"])
        self.assertEqual(resolve_category("speekers")["confidence"], "needs_review")

    def test_workspace_alias_does_not_modify_builtins(self):
        terms = [{"kind": "alias", "normalized_label": normalize_match("רמקולים"), "canonical_code": "powered_speaker"}]
        self.assertEqual(resolve_category("רמקולים", terms)["canonical_type"], "powered_speaker")
        self.assertEqual(resolve_category("רמקולים")["canonical_type"], "speaker")
