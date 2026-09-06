from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from inventory_dependencies import (
    DependencyNode,
    normalize_dependency_requirements,
    validate_dependency_updates,
)
from Item_node import Requirement


class TestInventoryDependencies(unittest.TestCase):
    def test_normalization_rejects_invalid_quantities_and_duplicates(self):
        for value in (0, -1, True, 1.5, "1.5", "no"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_dependency_requirements(
                        [{"item_id": "target", "amount": value}]
                    )
        with self.assertRaises(ValueError):
            normalize_dependency_requirements(
                [
                    {"item_id": "target", "amount": 1},
                    {"item_id": " TARGET ", "amount": 1},
                ]
            )

    def test_scope_visibility_and_cycle_detection(self):
        nodes = [
            DependencyNode("shared", None, "shared target", ()),
            DependencyNode(
                "personal",
                "owner-a",
                "personal target",
                (),
            ),
        ]
        validate_dependency_updates(
            nodes,
            {
                ("personal", "owner-a", "source"): (
                    Requirement("personal target", 1),
                    Requirement("shared target", 1),
                )
            },
        )
        with self.assertRaises(ValueError):
            validate_dependency_updates(
                nodes,
                {
                    ("personal", "owner-b", "source"): (
                        Requirement("personal target", 1),
                    )
                },
            )

        cycle_nodes = [
            DependencyNode("shared", None, "a", (Requirement("b"),)),
            DependencyNode("shared", None, "b", (Requirement("c"),)),
            DependencyNode("shared", None, "c", ()),
        ]
        with self.assertRaisesRegex(ValueError, "cycle"):
            validate_dependency_updates(
                cycle_nodes,
                {("shared", None, "c"): (Requirement("a"),)},
            )


if __name__ == "__main__":
    unittest.main()
