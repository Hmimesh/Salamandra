from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from Item_node import Requirement, normalize_item_id


@dataclass(frozen=True)
class EventTemplate:
    id: str
    name: str
    description: str
    items: tuple[Requirement, ...]

    def __post_init__(self):
        object.__setattr__(self, "id", normalize_item_id(self.id))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "items": [item.to_dict() for item in self.items],
        }


@dataclass(frozen=True)
class Kit:
    id: str
    name: str
    category: str
    description: str
    items: tuple[Requirement, ...]

    def __post_init__(self):
        object.__setattr__(self, "id", normalize_item_id(self.id))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "items": [item.to_dict() for item in self.items],
        }


class TemplateCatalog:
    def __init__(
        self,
        templates: list[EventTemplate] | None = None,
        kits: list[Kit] | None = None,
    ):
        self.templates = {template.id: template for template in templates or DEFAULT_TEMPLATES}
        self.kits = {kit.id: kit for kit in kits or DEFAULT_KITS}

    def get_template(self, template_id: str) -> EventTemplate | None:
        return self.templates.get(normalize_item_id(template_id))

    def get_kit(self, kit_id: str) -> Kit | None:
        return self.kits.get(normalize_item_id(kit_id))

    def to_dict(self) -> dict[str, Any]:
        return {
            "templates": [
                template.to_dict()
                for template in sorted(self.templates.values(), key=lambda item: item.name)
            ],
            "kits": [
                kit.to_dict()
                for kit in sorted(self.kits.values(), key=lambda item: item.name)
            ],
        }


DEFAULT_TEMPLATES = [
    EventTemplate(
        id="conference-basic",
        name="Conference Basic",
        description="Talks, panels, or lectures with microphones and PA.",
        items=(
            Requirement("pa speaker", 2),
            Requirement("shure sm58 microphone", 2),
            Requirement("microphone stand", 2),
        ),
    ),
    EventTemplate(
        id="live-show-small",
        name="Live Show Small",
        description="Small stage show with mixer, PA, microphones, and front wash.",
        items=(
            Requirement("small mixer", 1),
            Requirement("pa speaker", 2),
            Requirement("shure sm58 microphone", 4),
            Requirement("led par light", 6),
        ),
    ),
    EventTemplate(
        id="outdoor-power",
        name="Outdoor Power",
        description="Outdoor event power drop with distribution and cabling.",
        items=(
            Requirement("power distro 63a", 1),
            Requirement("power cable", 4),
        ),
    ),
    EventTemplate(
        id="brand-activation",
        name="Brand Activation",
        description="Promo booth or launch stand with light, display, and basic speech gear.",
        items=(
            Requirement("pa speaker", 2),
            Requirement("shure sm58 microphone", 1),
            Requirement("led par light", 4),
        ),
    ),
]


DEFAULT_KITS = [
    Kit(
        id="speech-kit",
        name="Speech Kit",
        category="audio",
        description="Two vocal mics, stands, and PA for talks.",
        items=(
            Requirement("shure sm58 microphone", 2),
            Requirement("microphone stand", 2),
            Requirement("pa speaker", 2),
        ),
    ),
    Kit(
        id="small-pa-kit",
        name="Small PA Kit",
        category="audio",
        description="Compact PA package for small rooms.",
        items=(
            Requirement("small mixer", 1),
            Requirement("pa speaker", 2),
        ),
    ),
    Kit(
        id="lighting-wash-kit",
        name="Lighting Wash Kit",
        category="lighting",
        description="Basic LED wash with control cabling.",
        items=(
            Requirement("led par light", 6),
            Requirement("dmx cable", 4),
        ),
    ),
    Kit(
        id="power-drop-kit",
        name="Power Drop Kit",
        category="power",
        description="Power distro and cable bundle for field setups.",
        items=(
            Requirement("power distro 63a", 1),
            Requirement("power cable", 4),
        ),
    ),
]
