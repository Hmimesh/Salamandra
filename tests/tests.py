import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from Inventory import Inventory
from Item_node import ItemNode, ItemType


class TestInventory(unittest.TestCase):
    def setUp(self):
        self.inventory = Inventory()

    def test_add_item_stores_new_item_with_lowercase_id(self):
        item = ItemNode("Mixer-01",ItemType.MIXER, 0, [])

        self.inventory.add_item(item)

        stored_item = self.inventory.get_item("mixer-01")
        self.assertIsNotNone(stored_item)
        self.assertEqual(stored_item.id, "mixer-01")
        self.assertEqual(stored_item.count, 1)

    def test_remove_item_deletes_item_when_stock_reaches_zero(self):
        item = ItemNode("Cable", ItemType.CABLE, 0, [])
        self.inventory.add_item(item)

        self.inventory.remove_item("cable")

        self.assertFalse(self.inventory.get_item("cable"))

    def test_use_item_updates_stock_and_in_use_count(self):
        item = ItemNode("Mic", ItemType.MIC, 0, [])
        self.inventory.add_item(item)

        self.inventory.use_item("mic")

        stored_item = self.inventory.get_item("mic")
        self.assertEqual(stored_item.count, 0)
        self.assertEqual(stored_item.in_use_count, 1)


if __name__ == "__main__":
    unittest.main()