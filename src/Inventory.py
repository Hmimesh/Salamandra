from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from Item_node import ItemNode, ItemType, normalize_item_id, parse_item_type


class ItemPreset(Protocol):
    def create_item(self, item_id: str | None = None, count: int | None = None) -> ItemNode:
        ...


class Inventory:
    def __init__(self, items: list[ItemNode] | None = None):
        self.items: dict[str, ItemNode] = {}

        for item in items or []:
            self.add_item(item, amount=item.count or 1)

    def add_item(
        self,
        item: ItemNode,
        amount: int | None = None,
        item_type: ItemType | str | None = None,
    ) -> ItemNode:
        amount = amount if amount is not None else item.count or 1
        if amount <= 0:
            raise ValueError("Amount must be greater than zero.")

        item.id = normalize_item_id(item.id)
        if item_type is not None:
            item.type = parse_item_type(item_type)

        existing_item = self.items.get(item.id)
        if existing_item:
            existing_item.add_stock(amount)
            if existing_item.type is None and item.type is not None:
                existing_item.type = item.type
            self._merge_metadata(existing_item, item)
            for requirement in item.req:
                if requirement not in existing_item.req:
                    existing_item.add_req(requirement)
            return existing_item

        item.count = 0
        item.add_stock(amount)
        self.items[item.id] = item
        return item

    def update_item(self, item_id: str, updated_item: ItemNode) -> ItemNode:
        current = self.get_item(item_id)
        if current is None:
            raise ValueError("Inventory item was not found.")

        old_id = current.id
        updated_item.id = normalize_item_id(updated_item.id)
        if updated_item.id != old_id and updated_item.id in self.items:
            raise ValueError("Another inventory item already uses that name.")

        updated_item.count = current.count
        updated_item.in_use_count = current.in_use_count
        if updated_item.id != old_id:
            del self.items[old_id]
        self.items[updated_item.id] = updated_item
        return updated_item

    def add_from_preset(
        self,
        preset: ItemPreset,
        item_id: str | None = None,
        amount: int | None = None,
    ) -> ItemNode:
        item = preset.create_item(item_id=item_id, count=amount)
        return self.add_item(item, amount=item.count)

    def remove_item(self, item_id: str, amount: int = 1) -> bool:
        item = self.get_item(item_id)
        if item is None:
            return False

        item.remove_stock(amount)
        if item.count == 0 and item.in_use_count == 0:
            del self.items[item.id]
        return True

    def get_item(self, item_id: str) -> ItemNode | None:
        return self.items.get(normalize_item_id(item_id))

    def use_item(self, item_id: str, amount: int = 1) -> bool:
        item = self.get_item(item_id)
        if item is None:
            return False

        item.mark_in_use(amount)
        return True

    def return_item(self, item_id: str, amount: int = 1) -> bool:
        item = self.get_item(item_id)
        if item is None:
            return False

        item.return_from_use(amount)
        return True

    def can_use_requirements(self, item_id: str, amount: int = 1) -> tuple[bool, list[str]]:
        item = self.get_item(item_id)
        if item is None:
            return False, [f"Item '{item_id}' does not exist."]

        missing = []
        for requirement in item.req:
            required_total = requirement.amount * amount
            required_item = self.get_item(requirement.item_id)
            available = required_item.count if required_item else 0
            if available < required_total:
                missing.append(
                    f"{requirement.item_id}: needs {required_total}, available {available}"
                )

        return len(missing) == 0, missing

    def list_items(self) -> list[ItemNode]:
        return sorted(self.items.values(), key=lambda item: item.id)

    def summary(self) -> dict[str, int]:
        total_in_stock = sum(item.count for item in self.items.values())
        total_in_use = sum(item.in_use_count for item in self.items.values())
        total_requirements = sum(len(item.req) for item in self.items.values())

        return {
            "unique_items": len(self.items),
            "in_stock": total_in_stock,
            "in_use": total_in_use,
            "requirements": total_requirements,
        }

    def migrate_preset_metadata(self, catalog) -> bool:
        changed = False
        for item in self.list_items():
            preset = catalog.find_for_item(item.id)
            if preset is None:
                continue
            template = preset.create_item(item_id=item.id, count=item.count)
            before = item.to_dict()
            self._merge_metadata(item, template)
            changed = changed or before != item.to_dict()
        return changed

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [item.to_dict() for item in self.list_items()],
            "summary": self.summary(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Inventory:
        inventory = cls()
        items_data = data.get("items", [])

        if isinstance(items_data, dict):
            items_data = items_data.values()

        for item_data in items_data:
            item = ItemNode.from_dict(item_data)
            inventory.items[item.id] = item

        return inventory

    def save_inventory(self, filename: str | Path):
        path = Path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", encoding="utf-8") as file:
            json.dump(self.to_dict(), file, indent=4)

    @classmethod
    def load_inventory(cls, filename: str | Path) -> Inventory:
        path = Path(filename)

        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)

        return cls.from_dict(data)

    def _merge_metadata(self, target: ItemNode, source: ItemNode):
        for attribute in (
            "class_id",
            "manufacturer",
            "model",
            "condition",
            "info",
        ):
            value = getattr(source, attribute)
            if value:
                setattr(target, attribute, value)
        if source.type is not None:
            target.type = source.type
        if source.capabilities:
            target.capabilities = source.capabilities
        if source.connectors:
            target.connectors = source.connectors
        if source.attributes:
            target.attributes.update(source.attributes)
        if source.quality_score:
            target.quality_score = source.quality_score
        if source.preference_score:
            target.preference_score = source.preference_score
        if source.weight_kg:
            target.weight_kg = source.weight_kg
