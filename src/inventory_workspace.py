from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from Inventory import Inventory
from Item_node import ItemNode, normalize_item_id


SHARED_SCOPE = "shared"
PERSONAL_SCOPE = "personal"


class InventoryWorkspace:
    def __init__(
        self,
        path: str | Path,
        legacy_inventory_path: str | Path | None = None,
    ):
        self.path = Path(path)
        self.legacy_inventory_path = Path(legacy_inventory_path) if legacy_inventory_path else None
        self.shared: dict[str, Inventory] = {}
        self.personal: dict[str, Inventory] = {}
        self._load()

    def inventory_for(
        self,
        scope: str,
        user_id: str | None = None,
        organization_id: str = "salamandra",
    ) -> Inventory:
        if scope == PERSONAL_SCOPE:
            if not user_id:
                raise ValueError("Personal inventory requires a signed-in account.")
            return self.personal.setdefault(user_id, Inventory())
        return self.shared.setdefault(organization_id, Inventory())

    def combined_inventory(
        self,
        user_id: str | None = None,
        organization_id: str = "salamandra",
    ) -> Inventory:
        combined = Inventory()
        self._merge_into(combined, self.shared.setdefault(organization_id, Inventory()))
        if user_id:
            self._merge_into(combined, self.personal.setdefault(user_id, Inventory()))
        return combined

    def add_item(
        self,
        item: ItemNode,
        amount: int,
        scope: str,
        user_id: str | None,
        organization_id: str = "salamandra",
        **_operation_context: Any,
    ):
        return self.inventory_for(scope, user_id, organization_id).add_item(
            item,
            amount=amount,
        )

    def add_from_preset(
        self,
        preset,
        scope: str,
        user_id: str | None,
        amount: int | None = None,
        organization_id: str = "salamandra",
        **_operation_context: Any,
    ):
        return self.inventory_for(scope, user_id, organization_id).add_from_preset(
            preset,
            amount=amount,
        )

    def use_item(
        self,
        item_id: str,
        amount: int,
        scope: str,
        user_id: str | None,
        organization_id: str = "salamandra",
        **_operation_context: Any,
    ) -> bool:
        return self.inventory_for(scope, user_id, organization_id).use_item(item_id, amount)

    def return_item(
        self,
        item_id: str,
        amount: int,
        scope: str,
        user_id: str | None,
        organization_id: str = "salamandra",
    ) -> bool:
        return self.inventory_for(scope, user_id, organization_id).return_item(item_id, amount)

    def remove_item(
        self,
        item_id: str,
        amount: int,
        scope: str,
        user_id: str | None,
        organization_id: str = "salamandra",
        **_operation_context: Any,
    ) -> bool:
        return self.inventory_for(scope, user_id, organization_id).remove_item(item_id, amount)

    def update_item(
        self,
        item_id: str,
        updated_item: ItemNode,
        scope: str,
        user_id: str | None,
        organization_id: str = "salamandra",
        **_operation_context: Any,
    ) -> ItemNode:
        return self.inventory_for(scope, user_id, organization_id).update_item(
            item_id,
            updated_item,
        )

    def replace_items(
        self,
        items: list[ItemNode],
        scope: str,
        user_id: str | None,
        organization_id: str,
        **_operation_context: Any,
    ):
        inventory = self.inventory_for(scope, user_id, organization_id)
        for item in items:
            inventory.items[item.id] = item

    def use_from_available_scopes(
        self,
        user_id: str | None,
        item_id: str,
        amount: int,
        organization_id: str = "salamandra",
    ):
        allocations = self.plan_scope_allocations(
            user_id,
            organization_id,
            [{"item_id": item_id, "amount": amount, "missing": 0}],
        )
        self.apply_scope_allocations(allocations, organization_id)
        return allocations

    def plan_scope_allocations(
        self,
        owner_user_id: str | None,
        organization_id: str,
        lines: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        capacities: dict[tuple[str, str, str], int] = {}
        for scope, user_id, inventory in self._scoped_inventories(
            owner_user_id,
            organization_id,
        ):
            for item in inventory.list_items():
                capacities[(scope, user_id or "", item.id)] = item.count

        allocations: list[dict[str, Any]] = []
        for line in lines:
            item_id = line.get("item_id", "")
            if not isinstance(item_id, str) or not item_id.strip():
                continue
            amount = int(line.get("amount", 0))
            if amount <= 0:
                raise ValueError("Plan line amount must be greater than zero.")
            if int(line.get("required_missing", line.get("missing", 0))) > 0:
                raise ValueError(f"Cannot dispatch missing stock for {item_id}.")
            normalized_id = normalize_item_id(item_id)
            remaining = amount
            for scope, user_id, _ in self._scoped_inventories(
                owner_user_id,
                organization_id,
            ):
                key = (scope, user_id or "", normalized_id)
                take = min(remaining, capacities.get(key, 0))
                if take <= 0:
                    continue
                allocations.append(
                    {
                        "scope": scope,
                        "owner_user_id": user_id or "",
                        "item_id": normalized_id,
                        "amount": take,
                    }
                )
                capacities[key] -= take
                remaining -= take
                if remaining == 0:
                    break
            if remaining:
                raise ValueError(
                    f"Not enough available stock for {normalized_id}; {remaining} missing."
                )
        return allocations

    def apply_scope_allocations(
        self,
        allocations: list[dict[str, Any]],
        organization_id: str,
    ):
        for allocation in allocations:
            inventory = self.inventory_for(
                allocation["scope"],
                allocation.get("owner_user_id") or None,
                organization_id,
            )
            if not inventory.use_item(allocation["item_id"], int(allocation["amount"])):
                raise ValueError(f"Inventory item {allocation['item_id']} was not found.")

    def return_scope_allocations(
        self,
        allocations: list[dict[str, Any]],
        organization_id: str,
    ):
        for allocation in allocations:
            inventory = self.inventory_for(
                allocation["scope"],
                allocation.get("owner_user_id") or None,
                organization_id,
            )
            item = inventory.get_item(allocation["item_id"])
            if item is None or item.in_use_count < int(allocation["amount"]):
                raise ValueError(
                    f"Cannot return {allocation['item_id']}; its dispatched stock is unavailable."
                )
        for allocation in allocations:
            inventory = self.inventory_for(
                allocation["scope"],
                allocation.get("owner_user_id") or None,
                organization_id,
            )
            inventory.return_item(allocation["item_id"], int(allocation["amount"]))

    def return_to_available_scopes(
        self,
        user_id: str | None,
        item_id: str,
        amount: int,
        organization_id: str = "salamandra",
    ):
        remaining = amount
        for inventory in self._ordered_inventories(user_id, organization_id):
            item = inventory.get_item(item_id)
            if item is None or item.in_use_count <= 0:
                continue
            returned = min(remaining, item.in_use_count)
            inventory.return_item(item_id, returned)
            remaining -= returned
            if remaining == 0:
                return
        raise ValueError(f"Cannot return {normalize_item_id(item_id)}; it is not marked in use.")

    def to_dict(
        self,
        user_id: str | None = None,
        organization_id: str | None = None,
    ) -> dict[str, Any]:
        personal_inventory = self.personal.setdefault(user_id, Inventory()) if user_id else Inventory()
        shared_inventory = (
            self.shared.setdefault(organization_id, Inventory())
            if organization_id
            else Inventory()
        )
        return {
            "shared": shared_inventory.to_dict(),
            "personal": personal_inventory.to_dict(),
            "combined": (
                self.combined_inventory(user_id, organization_id).to_dict()
                if organization_id
                else Inventory().to_dict()
            ),
        }

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as file:
            json.dump(
                {
                    "shared": {
                        organization_id: inventory.to_dict()
                        for organization_id, inventory in sorted(self.shared.items())
                    },
                    "personal": {
                        user_id: inventory.to_dict()
                        for user_id, inventory in sorted(self.personal.items())
                    },
                },
                file,
                indent=4,
            )

    def migrate_preset_metadata(self, catalog) -> bool:
        changed = False
        for inventory in [*self.shared.values(), *self.personal.values()]:
            changed = inventory.migrate_preset_metadata(catalog) or changed
        if changed:
            self.save()
        return changed

    def _load(self):
        if self.path.exists():
            with open(self.path, "r", encoding="utf-8") as file:
                data = json.load(file)
            shared_data = data.get("shared", {})
            if "items" in shared_data:
                self.shared = {"salamandra": Inventory.from_dict(shared_data)}
            else:
                self.shared = {
                    organization_id: Inventory.from_dict(inventory_data)
                    for organization_id, inventory_data in shared_data.items()
                }
            self.personal = {
                user_id: Inventory.from_dict(inventory_data)
                for user_id, inventory_data in data.get("personal", {}).items()
            }
            return

        if self.legacy_inventory_path and self.legacy_inventory_path.exists():
            self.shared = {
                "salamandra": Inventory.load_inventory(self.legacy_inventory_path)
            }

    def _ordered_inventories(
        self,
        user_id: str | None,
        organization_id: str,
    ) -> list[Inventory]:
        inventories = []
        if user_id:
            inventories.append(self.personal.setdefault(user_id, Inventory()))
        inventories.append(self.shared.setdefault(organization_id, Inventory()))
        return inventories

    def _scoped_inventories(
        self,
        user_id: str | None,
        organization_id: str,
    ) -> list[tuple[str, str | None, Inventory]]:
        inventories = []
        if user_id:
            inventories.append(
                (PERSONAL_SCOPE, user_id, self.personal.setdefault(user_id, Inventory()))
            )
        inventories.append(
            (SHARED_SCOPE, None, self.shared.setdefault(organization_id, Inventory()))
        )
        return inventories

    def _merge_into(self, target: Inventory, source: Inventory):
        for item in source.list_items():
            existing_item = target.get_item(item.id)
            if existing_item is None:
                target.items[item.id] = ItemNode.from_dict(item.to_dict())
                continue

            existing_item.count += item.count
            existing_item.in_use_count += item.in_use_count
            if not existing_item.info and item.info:
                existing_item.info = item.info
            if existing_item.type is None and item.type is not None:
                existing_item.type = item.type
            for attribute in (
                "class_id",
                "manufacturer",
                "model",
                "condition",
                "info",
            ):
                value = getattr(item, attribute)
                if value and not getattr(existing_item, attribute):
                    setattr(existing_item, attribute, value)
            existing_item.capabilities = tuple(
                sorted(set(existing_item.capabilities).union(item.capabilities))
            )
            existing_item.connectors = tuple(
                sorted(set(existing_item.connectors).union(item.connectors))
            )
            existing_item.attributes.update(item.attributes)
            existing_item.quality_score = max(
                existing_item.quality_score,
                item.quality_score,
            )
            existing_item.preference_score = max(
                existing_item.preference_score,
                item.preference_score,
            )
            existing_item.weight_kg = existing_item.weight_kg or item.weight_kg
            for requirement in item.req:
                if requirement not in existing_item.req:
                    existing_item.req.append(requirement)
