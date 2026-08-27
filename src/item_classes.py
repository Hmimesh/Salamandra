from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from Item_node import ItemNode, normalize_item_id


VALID_LEVELS = {"required", "recommended", "optional"}


def normalize_capability(value: str) -> str:
    normalized = value.strip().lower().replace(" ", ".")
    if not normalized:
        raise ValueError("Capability cannot be empty.")
    return normalized


@dataclass(frozen=True)
class CapabilityRequirement:
    capability: str
    amount: int = 1
    level: str = "required"
    source: str = "event"
    min_quality: int = 0
    required_connectors: tuple[str, ...] = field(default_factory=tuple)
    attributes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "capability", normalize_capability(self.capability))
        object.__setattr__(self, "level", self.level.strip().lower())
        object.__setattr__(
            self,
            "required_connectors",
            tuple(
                connector.strip().lower()
                for connector in self.required_connectors
                if connector.strip()
            ),
        )
        if self.amount <= 0:
            raise ValueError("Capability requirement amount must be greater than zero.")
        if self.level not in VALID_LEVELS:
            raise ValueError("Requirement level must be required, recommended, or optional.")
        if not 0 <= self.min_quality <= 100:
            raise ValueError("Minimum quality must be between 0 and 100.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "amount": self.amount,
            "level": self.level,
            "source": self.source,
            "min_quality": self.min_quality,
            "required_connectors": list(self.required_connectors),
            "attributes": self.attributes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CapabilityRequirement:
        return cls(
            capability=data.get("capability", ""),
            amount=int(data.get("amount", 1)),
            level=data.get("level", "required"),
            source=data.get("source", "event"),
            min_quality=int(data.get("min_quality", 0)),
            required_connectors=tuple(data.get("required_connectors", [])),
            attributes=dict(data.get("attributes", {})),
        )


@dataclass(frozen=True)
class DependencyRule:
    capability: str
    per_unit: float = 1
    fixed_amount: int = 0
    level: str = "required"
    note: str = ""

    def __post_init__(self):
        object.__setattr__(self, "capability", normalize_capability(self.capability))
        object.__setattr__(self, "level", self.level.strip().lower())
        if self.per_unit < 0 or self.fixed_amount < 0:
            raise ValueError("Dependency quantities cannot be negative.")
        if self.level not in VALID_LEVELS:
            raise ValueError("Dependency level must be required, recommended, or optional.")

    def amount_for(self, parent_amount: int) -> int:
        raw_amount = self.fixed_amount + (self.per_unit * parent_amount)
        return int(raw_amount) if raw_amount == int(raw_amount) else int(raw_amount) + 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "per_unit": self.per_unit,
            "fixed_amount": self.fixed_amount,
            "level": self.level,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DependencyRule:
        return cls(
            capability=data.get("capability", ""),
            per_unit=float(data.get("per_unit", 1)),
            fixed_amount=int(data.get("fixed_amount", 0)),
            level=data.get("level", "required"),
            note=data.get("note", ""),
        )


class ItemClass(ABC):
    @abstractmethod
    def match_score(
        self,
        item: ItemNode,
        requirement: CapabilityRequirement,
        context: dict[str, Any] | None = None,
    ) -> tuple[float, list[str]]:
        """Return a 0-100 suitability score and operator-readable reasons."""

    @abstractmethod
    def expand_requirements(
        self,
        amount: int,
        source: str,
    ) -> list[CapabilityRequirement]:
        """Expand the support gear needed by selected inventory units."""

    @abstractmethod
    def validate(self) -> list[str]:
        """Return validation errors for an editable class definition."""

    @abstractmethod
    def to_dict(self) -> dict[str, Any]:
        """Serialize the class for storage and the management UI."""


@dataclass
class ConfiguredItemClass(ItemClass):
    id: str
    name: str
    family: str
    capabilities: tuple[str, ...]
    organization_id: str = "system"
    aliases: tuple[str, ...] = field(default_factory=tuple)
    connectors: tuple[str, ...] = field(default_factory=tuple)
    dependency_rules: tuple[DependencyRule, ...] = field(default_factory=tuple)
    substitute_class_ids: tuple[str, ...] = field(default_factory=tuple)
    preference_weight: int = 50
    spare_factor: float = 0
    description: str = ""
    is_system: bool = False

    def __post_init__(self):
        self.id = normalize_item_id(self.id)
        self.name = self.name.strip()
        self.family = self.family.strip().lower()
        self.capabilities = tuple(
            normalize_capability(capability) for capability in self.capabilities
        )
        self.aliases = tuple(alias.strip().lower() for alias in self.aliases if alias.strip())
        self.connectors = tuple(
            connector.strip().lower() for connector in self.connectors if connector.strip()
        )
        self.substitute_class_ids = tuple(
            normalize_item_id(class_id)
            for class_id in self.substitute_class_ids
            if class_id.strip()
        )
        self.dependency_rules = tuple(
            rule if isinstance(rule, DependencyRule) else DependencyRule.from_dict(rule)
            for rule in self.dependency_rules
        )
        errors = self.validate()
        if errors:
            raise ValueError(" ".join(errors))

    def match_score(
        self,
        item: ItemNode,
        requirement: CapabilityRequirement,
        context: dict[str, Any] | None = None,
    ) -> tuple[float, list[str]]:
        item_capabilities = set(self.capabilities).union(item.capabilities)
        if requirement.capability not in item_capabilities:
            return 0, ["Capability does not match."]
        if item.quality_score < requirement.min_quality:
            return 0, ["Below the minimum quality for this event."]

        required_connectors = set(requirement.required_connectors)
        available_connectors = set(self.connectors).union(item.connectors)
        if required_connectors and not required_connectors.issubset(available_connectors):
            return 0, ["Connector requirement is not compatible."]
        for name, required_value in requirement.attributes.items():
            available_value = item.attributes.get(name)
            if isinstance(required_value, (int, float)):
                if not isinstance(available_value, (int, float)) or available_value < required_value:
                    return 0, [f"Needs {name.replace('_', ' ')} of at least {required_value}."]
            elif available_value != required_value:
                return 0, [f"Does not match required {name.replace('_', ' ')}."]

        context = context or {}
        capability_score = 40.0
        connector_score = 20.0
        quality = item.quality_score or 50
        suitability_score = 15.0 * min(max(quality, 0), 100) / 100
        availability_score = 15.0 if item.count > 0 else 0.0
        preference = item.preference_score or self.preference_weight
        preference_score = 10.0 * min(max(preference, 0), 100) / 100

        venue = str(context.get("venue_kind", ""))
        supported_venues = set(item.attributes.get("venue_kinds", []))
        if venue and supported_venues and venue not in supported_venues:
            suitability_score *= 0.65

        weather_rating = str(item.attributes.get("weather_rating", "indoor"))
        if context.get("outdoor") and weather_rating == "indoor":
            suitability_score *= 0.75

        handling_penalty = min(max(item.weight_kg - 20, 0) * 0.12, 7.0)
        total = max(
            1.0,
            capability_score
            + connector_score
            + suitability_score
            + availability_score
            + preference_score
            - handling_penalty,
        )
        reasons = [
            f"Matches {requirement.capability}",
            f"quality {quality}/100",
            f"preference {preference}/100",
        ]
        if handling_penalty:
            reasons.append(f"handling penalty for {item.weight_kg:g} kg")
        return round(total, 2), reasons

    def expand_requirements(
        self,
        amount: int,
        source: str,
    ) -> list[CapabilityRequirement]:
        return [
            CapabilityRequirement(
                capability=rule.capability,
                amount=rule.amount_for(amount),
                level=rule.level,
                source=source,
            )
            for rule in self.dependency_rules
            if rule.amount_for(amount) > 0
        ]

    def validate(self) -> list[str]:
        errors = []
        if not self.name:
            errors.append("Class name is required.")
        if not self.family:
            errors.append("Class family is required.")
        if not self.capabilities:
            errors.append("At least one capability is required.")
        if not 0 <= self.preference_weight <= 100:
            errors.append("Preference weight must be between 0 and 100.")
        if not 0 <= self.spare_factor <= 1:
            errors.append("Spare factor must be between 0 and 1.")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "family": self.family,
            "capabilities": list(self.capabilities),
            "organization_id": self.organization_id,
            "aliases": list(self.aliases),
            "connectors": list(self.connectors),
            "dependency_rules": [rule.to_dict() for rule in self.dependency_rules],
            "substitute_class_ids": list(self.substitute_class_ids),
            "preference_weight": self.preference_weight,
            "spare_factor": self.spare_factor,
            "description": self.description,
            "is_system": self.is_system,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConfiguredItemClass:
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            family=data.get("family", "other"),
            capabilities=tuple(data.get("capabilities", [])),
            organization_id=data.get("organization_id", "system"),
            aliases=tuple(data.get("aliases", [])),
            connectors=tuple(data.get("connectors", [])),
            dependency_rules=tuple(
                DependencyRule.from_dict(rule)
                for rule in data.get("dependency_rules", [])
            ),
            substitute_class_ids=tuple(data.get("substitute_class_ids", [])),
            preference_weight=int(data.get("preference_weight", 50)),
            spare_factor=float(data.get("spare_factor", 0)),
            description=data.get("description", ""),
            is_system=bool(data.get("is_system", False)),
        )


class ItemClassCatalog:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self.system_classes = {item_class.id: item_class for item_class in default_item_classes()}
        self.custom_classes: dict[str, dict[str, ConfiguredItemClass]] = {}
        self._load()

    def get(
        self,
        class_id: str,
        organization_id: str | None = None,
    ) -> ConfiguredItemClass | None:
        normalized_id = normalize_item_id(class_id)
        if organization_id:
            custom = self.custom_classes.get(organization_id, {}).get(normalized_id)
            if custom:
                return custom
        return self.system_classes.get(normalized_id)

    def list_classes(self, organization_id: str | None = None) -> list[ConfiguredItemClass]:
        classes = dict(self.system_classes)
        if organization_id:
            classes.update(self.custom_classes.get(organization_id, {}))
        return sorted(classes.values(), key=lambda item_class: (item_class.family, item_class.name))

    def upsert(
        self,
        item_class: ConfiguredItemClass,
        organization_id: str,
    ) -> ConfiguredItemClass:
        if not organization_id:
            raise ValueError("Organization is required for custom item classes.")
        stored = ConfiguredItemClass.from_dict(
            {
                **item_class.to_dict(),
                "organization_id": organization_id,
                "is_system": False,
            }
        )
        self.custom_classes.setdefault(organization_id, {})[stored.id] = stored
        self.save()
        return stored

    def clone(
        self,
        source_id: str,
        new_id: str,
        new_name: str,
        organization_id: str,
    ) -> ConfiguredItemClass:
        source = self.get(source_id, organization_id)
        if source is None:
            raise ValueError("The source item class was not found.")
        return self.upsert(
            ConfiguredItemClass.from_dict(
                {
                    **source.to_dict(),
                    "id": new_id,
                    "name": new_name,
                    "organization_id": organization_id,
                    "is_system": False,
                }
            ),
            organization_id,
        )

    def remove(self, class_id: str, organization_id: str) -> bool:
        removed = self.custom_classes.get(organization_id, {}).pop(
            normalize_item_id(class_id),
            None,
        )
        if removed:
            self.save()
        return removed is not None

    def to_dict(self, organization_id: str | None = None) -> dict[str, Any]:
        return {
            "classes": [
                item_class.to_dict()
                for item_class in self.list_classes(organization_id)
            ]
        }

    def save(self):
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as file:
            json.dump(
                {
                    "organizations": {
                        organization_id: {
                            class_id: item_class.to_dict()
                            for class_id, item_class in sorted(classes.items())
                        }
                        for organization_id, classes in sorted(self.custom_classes.items())
                    }
                },
                file,
                indent=4,
            )

    def _load(self):
        if self.path is None or not self.path.exists():
            return
        with open(self.path, "r", encoding="utf-8") as file:
            data = json.load(file)
        self.custom_classes = {
            organization_id: {
                class_id: ConfiguredItemClass.from_dict(class_data)
                for class_id, class_data in classes.items()
            }
            for organization_id, classes in data.get("organizations", {}).items()
        }


def default_item_classes() -> list[ConfiguredItemClass]:
    definitions = [
        {
            "id": "main-pa",
            "name": "Main PA speaker",
            "family": "pa",
            "capabilities": ("pa.main",),
            "aliases": ("pa", "speaker", "top"),
            "connectors": ("xlr", "power"),
            "dependency_rules": (
                DependencyRule("cable.xlr", 1),
                DependencyRule("cable.power", 1),
            ),
            "description": "Powered front-of-house loudspeaker.",
        },
        {
            "id": "stage-monitor",
            "name": "Stage monitor",
            "family": "pa",
            "capabilities": ("monitor.stage",),
            "aliases": ("monitor", "wedge", "foldback"),
            "connectors": ("xlr", "power"),
            "dependency_rules": (
                DependencyRule("cable.xlr", 1),
                DependencyRule("cable.power", 1),
            ),
            "description": "Powered stage wedge or foldback speaker.",
        },
        {
            "id": "wired-microphone",
            "name": "Wired microphone",
            "family": "microphone",
            "capabilities": ("microphone.vocal", "microphone.instrument"),
            "aliases": ("mic", "vocal mic", "instrument mic"),
            "connectors": ("xlr",),
            "dependency_rules": (
                DependencyRule("cable.xlr", 1),
                DependencyRule("stand.microphone", 1),
            ),
            "description": "Wired microphone suitable for vocals or instruments.",
        },
        {
            "id": "audio-mixer",
            "name": "Audio mixer",
            "family": "mixer",
            "capabilities": ("mixer.audio",),
            "aliases": ("desk", "console", "mixer"),
            "connectors": ("xlr", "power"),
            "dependency_rules": (DependencyRule("cable.power", 1),),
            "description": "Mixer with microphone inputs and monitor sends.",
        },
        {
            "id": "instrument-di",
            "name": "Instrument DI",
            "family": "di",
            "capabilities": ("di.instrument",),
            "aliases": ("di", "direct box"),
            "connectors": ("xlr", "jack"),
            "dependency_rules": (DependencyRule("cable.xlr", 1),),
            "description": "Direct box for acoustic and electronic instruments.",
        },
        {
            "id": "xlr-cable",
            "name": "XLR cable",
            "family": "cable",
            "capabilities": ("cable.xlr",),
            "aliases": ("xlr", "balanced cable"),
            "connectors": ("xlr",),
            "description": "Balanced XLR signal cable.",
        },
        {
            "id": "microphone-stand",
            "name": "Microphone stand",
            "family": "stand",
            "capabilities": ("stand.microphone",),
            "aliases": ("mic stand", "boom stand"),
            "description": "Stand or boom for a microphone position.",
        },
        {
            "id": "power-cable",
            "name": "Power cable",
            "family": "power",
            "capabilities": ("cable.power",),
            "aliases": ("iec", "power lead"),
            "connectors": ("power",),
            "description": "Mains power lead for powered equipment.",
        },
        {
            "id": "power-distribution",
            "name": "Power distribution",
            "family": "power",
            "capabilities": ("power.distribution",),
            "aliases": ("distro", "distribution"),
            "connectors": ("power",),
            "dependency_rules": (DependencyRule("cable.power", 2),),
            "description": "Event power distribution for field and stage use.",
        },
        {
            "id": "lighting-fixture",
            "name": "Lighting fixture",
            "family": "lighting",
            "capabilities": ("lighting.fixture",),
            "aliases": ("light", "par", "fixture"),
            "connectors": ("dmx", "power"),
            "dependency_rules": (
                DependencyRule("cable.power", 1),
                DependencyRule("cable.dmx", 1),
            ),
            "description": "Controllable stage or event lighting fixture.",
        },
        {
            "id": "dmx-cable",
            "name": "DMX cable",
            "family": "cable",
            "capabilities": ("cable.dmx",),
            "aliases": ("dmx", "lighting data"),
            "connectors": ("dmx",),
            "description": "Lighting control data cable.",
        },
        {
            "id": "truss",
            "name": "Truss",
            "family": "rigging",
            "capabilities": ("rigging.truss",),
            "aliases": ("truss", "rigging"),
            "description": "Load-rated event truss.",
        },
        {
            "id": "standard-vehicle",
            "name": "Standard operations vehicle",
            "family": "transport",
            "capabilities": ("transport.vehicle.standard",),
            "aliases": ("car", "estate car", "station wagon", "standard vehicle"),
            "description": "Passenger or estate vehicle suitable for a compact event load.",
        },
        {
            "id": "cargo-vehicle",
            "name": "Cargo vehicle",
            "family": "transport",
            "capabilities": ("transport.vehicle.cargo", "transport.vehicle.standard"),
            "aliases": ("van", "cargo van", "truck", "box truck"),
            "description": "Van or truck for bulky, heavy, or multi-department event loads.",
        },
        {
            "id": "utility-cart",
            "name": "Utility cart",
            "family": "transport",
            "capabilities": ("transport.cart",),
            "aliases": ("cart", "trolley", "hand truck"),
            "description": "Wheeled cart for loading equipment between vehicle, venue, and work area.",
        },
        {
            "id": "event-table",
            "name": "Event table",
            "family": "furniture",
            "capabilities": ("furniture.table",),
            "aliases": ("table", "folding table", "registration table"),
            "description": "Portable table for registration, hospitality, production, or service.",
        },
        {
            "id": "event-chair",
            "name": "Event chair",
            "family": "furniture",
            "capabilities": ("furniture.chair",),
            "aliases": ("chair", "folding chair", "seat"),
            "description": "Portable guest or operations seating.",
        },
        {
            "id": "crowd-barrier",
            "name": "Crowd barrier",
            "family": "site",
            "capabilities": ("site.barrier",),
            "aliases": ("barrier", "barricade", "queue barrier"),
            "description": "Portable barrier for queues, access control, and protected work areas.",
        },
        {
            "id": "event-shelter",
            "name": "Event shelter",
            "family": "site",
            "capabilities": ("site.shelter",),
            "aliases": ("canopy", "pop up canopy", "gazebo", "tent"),
            "description": "Portable weather shelter for staff, guests, or equipment.",
        },
        {
            "id": "projector",
            "name": "Projector",
            "family": "video",
            "capabilities": ("video.projector",),
            "aliases": ("projector", "beamer"),
            "dependency_rules": (DependencyRule("cable.power", 1),),
            "description": "Presentation projector for indoor event content.",
        },
        {
            "id": "projection-screen",
            "name": "Projection screen",
            "family": "video",
            "capabilities": ("video.screen",),
            "aliases": ("projection screen", "projector screen"),
            "description": "Portable screen for projected event content.",
        },
        {
            "id": "catering-station",
            "name": "Catering station",
            "family": "hospitality",
            "capabilities": ("hospitality.catering.station",),
            "aliases": ("catering station", "food station", "drink station"),
            "description": "Mobile service station for event food and beverage operations.",
        },
    ]
    return [
        ConfiguredItemClass(
            **definition,
            organization_id="system",
            is_system=True,
            preference_weight=70,
        )
        for definition in definitions
    ]
