from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from Item_node import ItemNode, ItemType, Requirement, normalize_item_id, parse_item_type


@dataclass(frozen=True)
class InventoryPreset:
    id: str
    name: str
    type: ItemType | str
    default_count: int = 1
    requirements: tuple[Requirement, ...] = field(default_factory=tuple)
    tags: tuple[str, ...] = field(default_factory=tuple)
    description: str = ""
    class_id: str = ""
    manufacturer: str = ""
    model: str = ""
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    connectors: tuple[str, ...] = field(default_factory=tuple)
    attributes: dict[str, Any] = field(default_factory=dict)
    quality_score: int = 70
    preference_score: int = 70
    weight_kg: float = 0

    def __post_init__(self):
        object.__setattr__(self, "id", normalize_item_id(self.id))
        object.__setattr__(self, "type", parse_item_type(self.type))
        object.__setattr__(
            self,
            "tags",
            tuple(tag.strip().lower() for tag in self.tags if tag.strip()),
        )
        object.__setattr__(
            self,
            "capabilities",
            tuple(capability.strip().lower() for capability in self.capabilities),
        )
        object.__setattr__(
            self,
            "connectors",
            tuple(connector.strip().lower() for connector in self.connectors),
        )
        if self.default_count <= 0:
            raise ValueError("Preset default count must be greater than zero.")

    def create_item(self, item_id: str | None = None, count: int | None = None) -> ItemNode:
        return ItemNode(
            id=item_id or self.name,
            type=self.type,
            count=count if count is not None else self.default_count,
            req=list(self.requirements),
            info=self.description,
            class_id=self.class_id,
            manufacturer=self.manufacturer,
            model=self.model,
            capabilities=self.capabilities,
            connectors=self.connectors,
            attributes=dict(self.attributes),
            quality_score=self.quality_score,
            preference_score=self.preference_score,
            weight_kg=self.weight_kg,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type.value,
            "default_count": self.default_count,
            "requirements": [requirement.to_dict() for requirement in self.requirements],
            "tags": list(self.tags),
            "description": self.description,
            "class_id": self.class_id,
            "manufacturer": self.manufacturer,
            "model": self.model,
            "capabilities": list(self.capabilities),
            "connectors": list(self.connectors),
            "attributes": self.attributes,
            "quality_score": self.quality_score,
            "preference_score": self.preference_score,
            "weight_kg": self.weight_kg,
        }


class PresetCatalog:
    def __init__(self, presets: list[InventoryPreset] | None = None):
        self.presets: dict[str, InventoryPreset] = {
            preset.id: preset for preset in presets or DEFAULT_PRESETS
        }

    def get(self, preset_id: str) -> InventoryPreset | None:
        return self.presets.get(normalize_item_id(preset_id))

    def find_for_item(self, item_id: str) -> InventoryPreset | None:
        normalized_item_id = normalize_item_id(item_id)
        direct_match = self.get(normalized_item_id)
        if direct_match is not None:
            return direct_match

        for preset in self.presets.values():
            if normalize_item_id(preset.name) == normalized_item_id:
                return preset

        return None

    def list_presets(self, tag: str | None = None) -> list[InventoryPreset]:
        presets = sorted(self.presets.values(), key=lambda preset: preset.name)
        if tag is None or tag == "all":
            return presets

        normalized_tag = tag.strip().lower()
        return [preset for preset in presets if normalized_tag in preset.tags]

    def to_dict(self) -> dict[str, Any]:
        tags = sorted({tag for preset in self.presets.values() for tag in preset.tags})
        return {
            "tags": ["all", *tags],
            "presets": [preset.to_dict() for preset in self.list_presets()],
        }


DEFAULT_PRESETS = [
    InventoryPreset(
        id="sm58-mic",
        name="Shure SM58 Microphone",
        type=ItemType.MIC,
        default_count=1,
        tags=("audio", "stage"),
        description="Standard vocal microphone.",
        class_id="wired-microphone",
        manufacturer="Shure",
        model="SM58",
        capabilities=("microphone.vocal", "microphone.instrument"),
        connectors=("xlr",),
        quality_score=88,
        preference_score=90,
        weight_kg=0.3,
    ),
    InventoryPreset(
        id="xlr-10m",
        name="XLR Cable 10m",
        type=ItemType.CABLE,
        default_count=1,
        tags=("audio", "cable"),
        description="Balanced microphone cable.",
        class_id="xlr-cable",
        capabilities=("cable.xlr",),
        connectors=("xlr",),
        quality_score=80,
    ),
    InventoryPreset(
        id="mic-stand",
        name="Microphone Stand",
        type=ItemType.STAND,
        default_count=1,
        tags=("audio", "stage"),
        description="General purpose boom stand.",
        class_id="microphone-stand",
        capabilities=("stand.microphone",),
        quality_score=75,
    ),
    InventoryPreset(
        id="di-box",
        name="DI Box",
        type=ItemType.DI,
        default_count=1,
        tags=("audio", "stage"),
        description="Direct input box for instruments.",
        class_id="instrument-di",
        capabilities=("di.instrument",),
        connectors=("xlr", "jack"),
        quality_score=78,
    ),
    InventoryPreset(
        id="small-mixer",
        name="Small Mixer",
        type=ItemType.MIXER,
        default_count=1,
        requirements=(Requirement("xlr cable 10m", 2),),
        tags=("audio", "control"),
        description="Compact mixer for small events.",
        class_id="audio-mixer",
        capabilities=("mixer.audio",),
        connectors=("xlr", "power"),
        attributes={"input_channels": 12, "monitor_sends": 4},
        quality_score=78,
        weight_kg=4.5,
    ),
    InventoryPreset(
        id="pa-speaker",
        name="PA Speaker",
        type=ItemType.PA,
        default_count=1,
        requirements=(Requirement("xlr cable 10m", 1),),
        tags=("audio", "stage"),
        description="Powered PA speaker.",
        class_id="main-pa",
        capabilities=("pa.main",),
        connectors=("xlr", "power"),
        attributes={"weather_rating": "indoor", "speaker_size_in": 15},
        quality_score=72,
        weight_kg=25,
    ),
    InventoryPreset(
        id="ev-zlx-15p",
        name="EV ZLX-15P",
        type=ItemType.PA,
        default_count=1,
        tags=("audio", "stage", "preferred"),
        description="Preferred 15-inch powered PA: clear, capable, and easy to handle.",
        class_id="main-pa",
        manufacturer="Electro-Voice",
        model="ZLX-15P",
        capabilities=("pa.main",),
        connectors=("xlr", "power"),
        attributes={"weather_rating": "covered", "speaker_size_in": 15},
        quality_score=94,
        preference_score=96,
        weight_kg=17.3,
    ),
    InventoryPreset(
        id="pro-tech-15",
        name="Pro Tech 15",
        type=ItemType.PA,
        default_count=1,
        tags=("audio", "stage", "substitute"),
        description="Serviceable 15-inch powered PA; heavier and less preferred than EV.",
        class_id="main-pa",
        manufacturer="Pro Tech",
        model="15",
        capabilities=("pa.main",),
        connectors=("xlr", "power"),
        attributes={"weather_rating": "covered", "speaker_size_in": 15},
        quality_score=65,
        preference_score=52,
        weight_kg=38,
    ),
    InventoryPreset(
        id="stage-monitor",
        name="Powered Stage Monitor",
        type=ItemType.PA,
        default_count=1,
        tags=("audio", "stage"),
        description="Powered stage wedge for performer monitoring.",
        class_id="stage-monitor",
        capabilities=("monitor.stage",),
        connectors=("xlr", "power"),
        quality_score=80,
        preference_score=82,
        weight_kg=16,
    ),
    InventoryPreset(
        id="led-par",
        name="LED Par Light",
        type=ItemType.LIGHTING,
        default_count=1,
        requirements=(Requirement("power cable", 1),),
        tags=("lighting", "stage"),
        description="LED wash fixture.",
        class_id="lighting-fixture",
        capabilities=("lighting.fixture",),
        connectors=("dmx", "power"),
        quality_score=72,
    ),
    InventoryPreset(
        id="dmx-cable",
        name="DMX Cable",
        type=ItemType.CABLE,
        default_count=1,
        tags=("lighting", "cable"),
        description="Control cable for lighting chain.",
        class_id="dmx-cable",
        capabilities=("cable.dmx",),
        connectors=("dmx",),
    ),
    InventoryPreset(
        id="power-cable",
        name="Power Cable",
        type=ItemType.POWER,
        default_count=1,
        tags=("power", "cable"),
        description="General power cable.",
        class_id="power-cable",
        capabilities=("cable.power",),
        connectors=("power",),
    ),
    InventoryPreset(
        id="power-distro-63a",
        name="Power Distro 63A",
        type=ItemType.POWER,
        default_count=1,
        requirements=(Requirement("power cable", 2),),
        tags=("power", "stage"),
        description="Main event power distribution.",
        class_id="power-distribution",
        capabilities=("power.distribution",),
        connectors=("power",),
        attributes={"weather_rating": "outdoor"},
        quality_score=85,
    ),
    InventoryPreset(
        id="truss-2m",
        name="Truss 2m",
        type=ItemType.RIGGING,
        default_count=1,
        tags=("rigging", "stage"),
        description="Two meter truss segment.",
        class_id="truss",
        capabilities=("rigging.truss",),
    ),
    InventoryPreset(
        id="stacking-case",
        name="Stacking Case",
        type=ItemType.CASE,
        default_count=1,
        tags=("storage", "case"),
        description="Large road case for equipment transport.",
    ),
    InventoryPreset(
        id="standard-estate-car",
        name="Standard Estate Car",
        type=ItemType.TRANSPORT,
        tags=("transport", "operations"),
        description="Compact operations vehicle for loads up to roughly 150 kg and 1.2 m3.",
        class_id="standard-vehicle",
        capabilities=("transport.vehicle.standard",),
        attributes={"payload_capacity_kg": 150, "cargo_volume_m3": 1.2},
        quality_score=88,
        preference_score=99,
    ),
    InventoryPreset(
        id="cargo-van",
        name="Cargo Van",
        type=ItemType.TRANSPORT,
        tags=("transport", "operations"),
        description="Cargo vehicle for heavy or multi-department event loads.",
        class_id="cargo-vehicle",
        capabilities=("transport.vehicle.cargo", "transport.vehicle.standard"),
        attributes={"payload_capacity_kg": 900, "cargo_volume_m3": 6.0},
        quality_score=88,
        preference_score=76,
    ),
    InventoryPreset(
        id="utility-cart",
        name="Utility Cart",
        type=ItemType.TRANSPORT,
        default_count=1,
        tags=("transport", "warehouse", "operations"),
        description="Foldable platform cart for venue load-in and load-out.",
        class_id="utility-cart",
        capabilities=("transport.cart",),
        attributes={"payload_capacity_kg": 250},
        quality_score=80,
        preference_score=90,
        weight_kg=14,
    ),
    InventoryPreset(
        id="folding-table",
        name="Folding Table",
        type=ItemType.FURNITURE,
        tags=("furniture", "operations", "hospitality"),
        description="Six-foot folding table for registration, catering, or production.",
        class_id="event-table",
        capabilities=("furniture.table",),
        attributes={"volume_m3": 0.12},
        weight_kg=12,
    ),
    InventoryPreset(
        id="folding-chair",
        name="Folding Chair",
        type=ItemType.FURNITURE,
        tags=("furniture", "guest"),
        description="Stackable folding chair for guests or event staff.",
        class_id="event-chair",
        capabilities=("furniture.chair",),
        attributes={"volume_m3": 0.05},
        weight_kg=4.5,
    ),
    InventoryPreset(
        id="crowd-barrier",
        name="Crowd Barrier",
        type=ItemType.BARRIER,
        tags=("site", "safety", "operations"),
        description="Portable crowd-control barrier for queues and protected areas.",
        class_id="crowd-barrier",
        capabilities=("site.barrier",),
        attributes={"volume_m3": 0.10},
        weight_kg=14,
    ),
    InventoryPreset(
        id="pop-up-canopy",
        name="Pop-up Canopy",
        type=ItemType.OTHER,
        tags=("site", "outdoor", "operations"),
        description="Three-meter portable canopy for staff, equipment, or guest shelter.",
        class_id="event-shelter",
        capabilities=("site.shelter",),
        attributes={"volume_m3": 0.30, "weather_rating": "outdoor"},
        weight_kg=22,
    ),
    InventoryPreset(
        id="presentation-projector",
        name="Presentation Projector",
        type=ItemType.VIDEO,
        tags=("video", "conference"),
        description="Portable projector for presentations and event content.",
        class_id="projector",
        capabilities=("video.projector",),
        connectors=("hdmi", "power"),
        attributes={"volume_m3": 0.03},
        weight_kg=4,
    ),
    InventoryPreset(
        id="projection-screen",
        name="Projection Screen",
        type=ItemType.DISPLAY,
        tags=("video", "conference"),
        description="Portable projection screen with stand.",
        class_id="projection-screen",
        capabilities=("video.screen",),
        attributes={"volume_m3": 0.12},
        weight_kg=10,
    ),
    InventoryPreset(
        id="mobile-catering-station",
        name="Mobile Catering Station",
        type=ItemType.CATERING,
        tags=("hospitality", "operations"),
        description="Mobile service station for food and beverage operations.",
        class_id="catering-station",
        capabilities=("hospitality.catering.station",),
        attributes={"volume_m3": 0.20},
        weight_kg=30,
    ),
]
