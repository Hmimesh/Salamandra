from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from Item_node import Requirement, normalize_item_id
from security import StateConflict


def normalize_kit_name(value: str) -> str:
    return " ".join(value.split()).casefold()


@dataclass
class SavedKit:
    name: str
    description: str
    organization_id: str
    items: list[Requirement]
    notes: str = ""
    id: str = field(default_factory=lambda: uuid4().hex)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "organization_id": self.organization_id,
            "items": [item.to_dict() for item in self.items],
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SavedKit:
        return cls(
            id=str(data.get("id") or uuid4().hex),
            name=str(data.get("name", "")).strip(),
            description=str(data.get("description", "")).strip(),
            organization_id=str(data.get("organization_id", "")).strip(),
            items=[Requirement.from_dict(item) for item in data.get("items", [])],
            notes=str(data.get("notes", "")).strip(),
        )


def validate_kit_payload(data: dict[str, Any], organization_id: str) -> SavedKit:
    name = " ".join(str(data.get("name", "")).split())
    description = str(data.get("description", "")).strip()
    notes = str(data.get("notes", "")).strip()
    if not name or len(name) > 200:
        raise ValueError("Kit name is required and must be 200 characters or fewer.")
    if len(description) > 1_000 or len(notes) > 1_000:
        raise ValueError("Kit description and notes must be 1,000 characters or fewer.")
    totals: dict[str, int] = {}
    raw_items = data.get("items", [])
    if not isinstance(raw_items, list):
        raise ValueError("Kit items must be a list.")
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ValueError("Each kit item must be an object.")
        item_id = normalize_item_id(str(raw.get("item_id", "")))
        amount = int(raw.get("amount", 0))
        if not item_id or amount < 1 or amount > 10_000:
            raise ValueError("Each kit item needs an inventory item and positive quantity.")
        totals[item_id] = totals.get(item_id, 0) + amount
    if not totals:
        raise ValueError("Add at least one inventory item to the kit.")
    return SavedKit(
        name=name,
        description=description,
        organization_id=organization_id,
        items=[Requirement(item_id=item_id, amount=amount) for item_id, amount in sorted(totals.items())],
        notes=notes,
    )


class KitStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.kits = self._load()

    def list_kits(self, organization_id: str) -> list[SavedKit]:
        return sorted(
            (kit for kit in self.kits.values() if kit.organization_id == organization_id),
            key=lambda kit: (kit.name.lower(), kit.id),
        )

    def get(self, kit_id: str, organization_id: str) -> SavedKit | None:
        kit = self.kits.get(str(kit_id))
        return kit if kit and kit.organization_id == organization_id else None

    def create(self, data: dict[str, Any], organization_id: str) -> SavedKit:
        kit = validate_kit_payload(data, organization_id)
        if any(
            normalize_kit_name(existing.name) == normalize_kit_name(kit.name)
            for existing in self.list_kits(organization_id)
        ):
            raise StateConflict("A kit with this name already exists in the workspace.")
        self.kits[kit.id] = kit
        self.save()
        return kit

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as file:
            json.dump({"kits": [kit.to_dict() for kit in self.kits.values()]}, file, indent=2)

    def _load(self) -> dict[str, SavedKit]:
        if not self.path.exists():
            return {}
        with open(self.path, "r", encoding="utf-8") as file:
            data = json.load(file)
        return {kit.id: kit for kit in (SavedKit.from_dict(item) for item in data.get("kits", []))}
