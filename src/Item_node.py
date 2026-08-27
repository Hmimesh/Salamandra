from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ItemType(Enum):
    MIXER = "mixer"
    PA = "pa"
    MIC = "microphone"
    LIGHTING = "lighting"
    POWER = "power"
    RIGGING = "rigging"
    VIDEO = "video"
    DI = "di"
    BACKLINE = "backline"
    STAND = "stand"
    CABLE = "cable"
    CASE = "case"
    ACCESSORY = "accessory"
    FURNITURE = "furniture"
    DECOR = "decor"
    CATERING = "catering"
    TOOL = "tool"
    TRANSPORT = "transport"
    DISPLAY = "display"
    BARRIER = "barrier"
    OTHER = "other"


def normalize_item_id(item_id: str) -> str:
    normalized = item_id.strip().lower()
    if not normalized:
        raise ValueError("Item id cannot be empty.")
    return normalized


def parse_item_type(value: ItemType | str | None) -> ItemType | None:
    if value is None or isinstance(value, ItemType):
        return value

    try:
        return ItemType(value.strip().lower())
    except ValueError as error:
        valid_types = ", ".join(item_type.value for item_type in ItemType)
        raise ValueError(f"Invalid item type '{value}'. Expected one of: {valid_types}.") from error


@dataclass
class Requirement:
    item_id: str
    amount: int = 1

    def __post_init__(self):
        self.item_id = normalize_item_id(self.item_id)
        if self.amount <= 0:
            raise ValueError("Requirement amount must be greater than zero.")

    @property
    def item(self):
        return self.item_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "amount": self.amount,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Requirement:
        item_id = data.get("item_id", data.get("item", ""))
        return cls(item_id=item_id, amount=int(data.get("amount", 1)))

    def __repr__(self):
        return f"{self.amount}x {self.item_id}"


@dataclass
class ItemNode:
    id: str = ""
    type: ItemType | str | None = None
    count: int = 0
    req: list[Requirement] = field(default_factory=list)
    info: str = ""
    quality_score: int = 0
    in_use_count: int = 0
    class_id: str = ""
    manufacturer: str = ""
    model: str = ""
    condition: str = "ready"
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    connectors: tuple[str, ...] = field(default_factory=tuple)
    attributes: dict[str, Any] = field(default_factory=dict)
    preference_score: int = 0
    weight_kg: float = 0

    def __post_init__(self):
        if self.id:
            self.id = normalize_item_id(self.id)
        self.type = parse_item_type(self.type)
        self.req = [self._parse_requirement(requirement) for requirement in self.req]
        self.class_id = normalize_item_id(self.class_id) if self.class_id else ""
        self.capabilities = tuple(
            capability.strip().lower().replace(" ", ".")
            for capability in self.capabilities
            if capability.strip()
        )
        self.connectors = tuple(
            connector.strip().lower()
            for connector in self.connectors
            if connector.strip()
        )
        self.manufacturer = self.manufacturer.strip()
        self.model = self.model.strip()
        self.condition = self.condition.strip().lower() or "ready"
        self._validate_counts()

    def _validate_counts(self):
        if self.count < 0:
            raise ValueError("Item count cannot be negative.")
        if self.in_use_count < 0:
            raise ValueError("In-use count cannot be negative.")
        if self.quality_score < 0:
            raise ValueError("Quality score cannot be negative.")
        if self.quality_score > 100:
            raise ValueError("Quality score cannot exceed 100.")
        if not 0 <= self.preference_score <= 100:
            raise ValueError("Preference score must be between 0 and 100.")
        if self.weight_kg < 0:
            raise ValueError("Item weight cannot be negative.")

    def add_req(self, req: Requirement):
        self.req.append(self._parse_requirement(req))

    def add_stock(self, amount: int = 1):
        if amount <= 0:
            raise ValueError("Stock amount must be greater than zero.")
        self.count += amount

    def remove_stock(self, amount: int = 1):
        if amount <= 0:
            raise ValueError("Stock amount must be greater than zero.")
        if amount > self.count:
            raise ValueError(f"Cannot remove {amount} from {self.id}; only {self.count} available.")
        self.count -= amount

    def mark_in_use(self, amount: int = 1):
        self.remove_stock(amount)
        self.in_use_count += amount

    def return_from_use(self, amount: int = 1):
        if amount <= 0:
            raise ValueError("Return amount must be greater than zero.")
        if amount > self.in_use_count:
            raise ValueError(
                f"Cannot return {amount} of {self.id}; only {self.in_use_count} in use."
            )
        self.in_use_count -= amount
        self.count += amount

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value if self.type else None,
            "count": self.count,
            "info": self.info,
            "quality_score": self.quality_score,
            "in_use_count": self.in_use_count,
            "requirements": [requirement.to_dict() for requirement in self.req],
            "class_id": self.class_id,
            "manufacturer": self.manufacturer,
            "model": self.model,
            "condition": self.condition,
            "capabilities": list(self.capabilities),
            "connectors": list(self.connectors),
            "attributes": self.attributes,
            "preference_score": self.preference_score,
            "weight_kg": self.weight_kg,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ItemNode:
        return cls(
            id=data.get("id", ""),
            type=data.get("type"),
            count=int(data.get("count", 0)),
            info=data.get("info", ""),
            quality_score=int(data.get("quality_score", 0)),
            in_use_count=int(data.get("in_use_count", 0)),
            class_id=data.get("class_id", ""),
            manufacturer=data.get("manufacturer", ""),
            model=data.get("model", ""),
            condition=data.get("condition", "ready"),
            capabilities=tuple(data.get("capabilities", [])),
            connectors=tuple(data.get("connectors", [])),
            attributes=dict(data.get("attributes", {})),
            preference_score=int(data.get("preference_score", 0)),
            weight_kg=float(data.get("weight_kg", 0)),
            req=[
                Requirement.from_dict(requirement)
                for requirement in data.get("requirements", data.get("req", []))
            ],
        )

    @staticmethod
    def _parse_requirement(requirement: Requirement | dict[str, Any]) -> Requirement:
        if isinstance(requirement, Requirement):
            return requirement
        if isinstance(requirement, dict):
            return Requirement.from_dict(requirement)
        raise TypeError("Requirement must be a Requirement object or dictionary.")
