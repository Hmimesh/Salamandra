from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil
from typing import Any

from Inventory import Inventory
from Item_node import ItemNode, Requirement, normalize_item_id
from item_classes import CapabilityRequirement, ItemClassCatalog
from presets import InventoryPreset, PresetCatalog


@dataclass
class EventPlanLine:
    item_id: str
    amount: int
    available: int
    missing: int
    source: str
    type: str | None = None
    preset_id: str | None = None
    reserved_elsewhere: int = 0
    conflict: bool = False
    capability: str = ""
    level: str = "required"
    selected_class_id: str = ""
    score: float = 0
    reasons: list[str] = field(default_factory=list)
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    substitution: bool = False
    required_amount: int = 0
    recommended_amount: int = 0
    optional_amount: int = 0
    required_missing: int = 0
    recommended_missing: int = 0
    optional_missing: int = 0
    components: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self):
        if not (self.required_amount or self.recommended_amount or self.optional_amount):
            setattr(self, f"{self.level}_amount", self.amount)
        if self.missing and not (
            self.required_missing or self.recommended_missing or self.optional_missing
        ):
            setattr(self, f"{self.level}_missing", self.missing)
        if not self.components and (self.capability or self.source):
            self.components = [
                {
                    "capability": self.capability,
                    "level": self.level,
                    "amount": self.amount,
                    "missing": self.missing,
                    "source": self.source,
                }
            ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "amount": self.amount,
            "available": self.available,
            "missing": self.missing,
            "source": self.source,
            "type": self.type,
            "preset_id": self.preset_id,
            "reserved_elsewhere": self.reserved_elsewhere,
            "conflict": self.conflict,
            "capability": self.capability,
            "level": self.level,
            "selected_class_id": self.selected_class_id,
            "score": self.score,
            "reasons": self.reasons,
            "alternatives": self.alternatives,
            "substitution": self.substitution,
            "required_amount": self.required_amount,
            "recommended_amount": self.recommended_amount,
            "optional_amount": self.optional_amount,
            "required_missing": self.required_missing,
            "recommended_missing": self.recommended_missing,
            "optional_missing": self.optional_missing,
            "components": self.components,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EventPlanLine:
        return cls(
            item_id=data.get("item_id", ""),
            amount=int(data.get("amount", 1)),
            available=int(data.get("available", 0)),
            missing=int(data.get("missing", 0)),
            source=data.get("source", "event"),
            type=data.get("type"),
            preset_id=data.get("preset_id"),
            reserved_elsewhere=int(data.get("reserved_elsewhere", 0)),
            conflict=bool(data.get("conflict", False)),
            capability=data.get("capability", ""),
            level=data.get("level", "required"),
            selected_class_id=data.get("selected_class_id", ""),
            score=float(data.get("score", 0)),
            reasons=list(data.get("reasons", [])),
            alternatives=list(data.get("alternatives", [])),
            substitution=bool(data.get("substitution", False)),
            required_amount=int(data.get("required_amount", 0)),
            recommended_amount=int(data.get("recommended_amount", 0)),
            optional_amount=int(data.get("optional_amount", 0)),
            required_missing=int(data.get("required_missing", 0)),
            recommended_missing=int(data.get("recommended_missing", 0)),
            optional_missing=int(data.get("optional_missing", 0)),
            components=list(data.get("components", [])),
        )


@dataclass
class EventPlan:
    requested_items: list[Requirement]
    lines: list[EventPlanLine] = field(default_factory=list)
    unresolved_items: list[str] = field(default_factory=list)
    capability_requirements: list[CapabilityRequirement] = field(default_factory=list)
    reallocations: list[dict[str, Any]] = field(default_factory=list)
    allocation_updates: list[dict[str, Any]] = field(default_factory=list)
    transport_summary: dict[str, Any] = field(default_factory=dict)

    @property
    def is_ready(self) -> bool:
        return (
            all(line.required_missing == 0 for line in self.lines)
            and not self.unresolved_items
        )

    @property
    def total_missing(self) -> int:
        return sum(line.required_missing for line in self.lines)

    @property
    def recommended_missing(self) -> int:
        return sum(line.recommended_missing for line in self.lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_items": [
                requirement.to_dict() for requirement in self.requested_items
            ],
            "lines": [line.to_dict() for line in self.lines],
            "unresolved_items": self.unresolved_items,
            "capability_requirements": [
                requirement.to_dict() for requirement in self.capability_requirements
            ],
            "reallocations": self.reallocations,
            "allocation_updates": self.allocation_updates,
            "transport_summary": self.transport_summary,
            "is_ready": self.is_ready,
            "total_missing": self.total_missing,
            "recommended_missing": self.recommended_missing,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EventPlan:
        return cls(
            requested_items=[
                Requirement.from_dict(requirement)
                for requirement in data.get("requested_items", [])
            ],
            lines=[
                EventPlanLine.from_dict(line)
                for line in data.get("lines", [])
            ],
            unresolved_items=list(data.get("unresolved_items", [])),
            capability_requirements=[
                CapabilityRequirement.from_dict(requirement)
                for requirement in data.get("capability_requirements", [])
            ],
            reallocations=list(data.get("reallocations", [])),
            allocation_updates=list(data.get("allocation_updates", [])),
            transport_summary=dict(data.get("transport_summary", {})),
        )


class EventPlanner:
    def __init__(
        self,
        inventory: Inventory,
        catalog: PresetCatalog,
        reserved_counts: dict[str, int] | None = None,
        item_classes: ItemClassCatalog | None = None,
        organization_id: str = "salamandra",
    ):
        self.inventory = inventory
        self.catalog = catalog
        self.reserved_counts = reserved_counts or {}
        self.item_classes = item_classes or ItemClassCatalog()
        self.organization_id = organization_id

    def build_plan(self, requests: list[Requirement]) -> EventPlan:
        totals: dict[str, int] = {}
        sources: dict[str, set[str]] = {}
        unresolved_items: set[str] = set()

        for request in requests:
            self._add_required_item(
                item_id=request.item_id,
                amount=request.amount,
                source="event",
                totals=totals,
                sources=sources,
                unresolved_items=unresolved_items,
                path=[],
            )

        lines = [
            self._make_line(item_id, amount, sources[item_id])
            for item_id, amount in sorted(totals.items())
        ]

        return EventPlan(
            requested_items=requests,
            lines=lines,
            unresolved_items=sorted(unresolved_items),
        )

    def build_capability_plan(
        self,
        requirements: list[CapabilityRequirement],
        context: dict[str, Any] | None = None,
    ) -> EventPlan:
        event = {
            "id": str((context or {}).get("id", "draft")),
            "title": str((context or {}).get("title", "Draft event")),
            "priority_score": int((context or {}).get("priority_score", 50)),
            "event_size": str((context or {}).get("event_size", "medium")),
            "capability_requirements": [
                requirement.to_dict() for requirement in requirements
            ],
            **(context or {}),
        }
        return self.allocate_overlapping_events([event])[event["id"]]

    def allocate_overlapping_events(
        self,
        events: list[dict[str, Any]],
    ) -> dict[str, EventPlan]:
        remaining = {
            item.id: max(item.count - self.reserved_counts.get(item.id, 0), 0)
            for item in self.inventory.list_items()
        }
        plans: dict[str, EventPlan] = {}
        locked_events = [
            event for event in events if event.get("status") in {"packed", "out"}
        ]
        for event in locked_events:
            event_id = str(event.get("id", "")) or "locked"
            locked_plan = EventPlan.from_dict(event.get("plan", {}))
            plans[event_id] = locked_plan
            for line in locked_plan.lines:
                if line.item_id and line.missing == 0:
                    remaining[line.item_id] = max(
                        remaining.get(line.item_id, 0) - line.amount,
                        0,
                    )

        ordered_events = sorted(
            [event for event in events if event not in locked_events],
            key=lambda event: (
                -int(event.get("priority_score", 50)),
                str(event.get("title", "")),
                str(event.get("id", "")),
            ),
        )

        for event in ordered_events:
            event_id = str(event.get("id", "")) or "draft"
            requirements = [
                requirement
                if isinstance(requirement, CapabilityRequirement)
                else CapabilityRequirement.from_dict(requirement)
                for requirement in event.get("capability_requirements", [])
            ]
            plan = self._allocate_capabilities(requirements, event, remaining)
            plans[event_id] = plan

        return plans

    def _allocate_capabilities(
        self,
        requirements: list[CapabilityRequirement],
        context: dict[str, Any],
        remaining: dict[str, int],
    ) -> EventPlan:
        queue = list(requirements)
        raw_lines: list[EventPlanLine] = []
        required_xlr = 0

        while queue:
            requirement = queue.pop(0)
            if requirement.level == "required" and requirement.capability == "cable.xlr":
                required_xlr += requirement.amount
            candidates = self._rank_candidates(requirement, context, remaining)
            needed = requirement.amount

            for index, candidate in enumerate(candidates):
                if needed <= 0:
                    break
                item = candidate["item"]
                available = remaining.get(item.id, 0)
                if available <= 0:
                    continue
                assigned = min(needed, available)
                remaining[item.id] = available - assigned
                needed -= assigned
                alternatives = [
                    self._candidate_payload(ranked_item)
                    for ranked_item in candidates
                    if ranked_item["item"].id != item.id
                ]
                raw_lines.append(
                    EventPlanLine(
                        item_id=item.id,
                        amount=assigned,
                        available=item.count,
                        missing=0,
                        source=requirement.source,
                        type=item.type.value if item.type else None,
                        reserved_elsewhere=item.count - available,
                        capability=requirement.capability,
                        level=requirement.level,
                        selected_class_id=candidate["item_class"].id,
                        score=candidate["score"],
                        reasons=candidate["reasons"],
                        alternatives=alternatives,
                        substitution=index > 0,
                    )
                )
                queue.extend(
                    candidate["item_class"].expand_requirements(
                        assigned,
                        source=item.id,
                    )
                )
                if requirement.level == "required" and candidate["item_class"].spare_factor:
                    queue.append(
                        CapabilityRequirement(
                            requirement.capability,
                            max(1, ceil(assigned * candidate["item_class"].spare_factor)),
                            level="recommended",
                            source=f"{item.id} spare policy",
                            min_quality=requirement.min_quality,
                            required_connectors=requirement.required_connectors,
                            attributes=requirement.attributes,
                        )
                    )

            if needed > 0:
                raw_lines.append(
                    EventPlanLine(
                        item_id="",
                        amount=needed,
                        available=requirement.amount - needed,
                        missing=needed,
                        source=requirement.source,
                        capability=requirement.capability,
                        level=requirement.level,
                        conflict=requirement.level == "required",
                        reasons=["No sufficient matching stock remains for this time window."],
                        alternatives=[
                            self._candidate_payload(candidate)
                            for candidate in candidates
                        ],
                    )
                )

        if required_xlr:
            spare_requirement = CapabilityRequirement(
                capability="cable.xlr",
                amount=max(1, ceil(required_xlr * 0.2)),
                level="recommended",
                source="20% cable spare",
            )
            raw_lines.extend(
                self._allocate_single_requirement(spare_requirement, context, remaining)
            )

        transport_summary, transport_requirements = self._transport_plan(raw_lines, context)
        for requirement in transport_requirements:
            raw_lines.extend(
                self._allocate_single_requirement(requirement, context, remaining)
            )

        return EventPlan(
            requested_items=[],
            lines=self._combine_plan_lines(raw_lines),
            capability_requirements=[*requirements, *transport_requirements],
            transport_summary=transport_summary,
        )

    def _allocate_single_requirement(
        self,
        requirement: CapabilityRequirement,
        context: dict[str, Any],
        remaining: dict[str, int],
    ) -> list[EventPlanLine]:
        candidates = self._rank_candidates(requirement, context, remaining)
        needed = requirement.amount
        lines = []
        for index, candidate in enumerate(candidates):
            item = candidate["item"]
            available = remaining.get(item.id, 0)
            if available <= 0 or needed <= 0:
                continue
            assigned = min(needed, available)
            remaining[item.id] -= assigned
            needed -= assigned
            lines.append(
                EventPlanLine(
                    item_id=item.id,
                    amount=assigned,
                    available=item.count,
                    missing=0,
                    source=requirement.source,
                    type=item.type.value if item.type else None,
                    capability=requirement.capability,
                    level=requirement.level,
                    selected_class_id=candidate["item_class"].id,
                    score=candidate["score"],
                    reasons=candidate["reasons"],
                    alternatives=[
                        self._candidate_payload(other)
                        for other in candidates
                        if other["item"].id != item.id
                    ],
                    substitution=index > 0,
                )
            )
        if needed:
            lines.append(
                EventPlanLine(
                    item_id="",
                    amount=needed,
                    available=requirement.amount - needed,
                    missing=needed,
                    source=requirement.source,
                    capability=requirement.capability,
                    level=requirement.level,
                    conflict=requirement.level == "required",
                    alternatives=[self._candidate_payload(candidate) for candidate in candidates],
                )
            )
        return lines

    def _rank_candidates(
        self,
        requirement: CapabilityRequirement,
        context: dict[str, Any],
        remaining: dict[str, int],
    ) -> list[dict[str, Any]]:
        candidates = []
        for item in self.inventory.list_items():
            item_class = self.item_classes.get(item.class_id, self.organization_id) if item.class_id else None
            if item_class is None:
                continue
            score, reasons = item_class.match_score(item, requirement, context)
            if score <= 0:
                continue
            candidates.append(
                {
                    "item": item,
                    "item_class": item_class,
                    "score": score,
                    "reasons": reasons,
                    "remaining": remaining.get(item.id, 0),
                }
            )
        return sorted(
            candidates,
            key=lambda candidate: (
                -candidate["score"],
                -candidate["remaining"],
                candidate["item"].id,
            ),
        )

    def _candidate_payload(self, candidate: dict[str, Any]) -> dict[str, Any]:
        item = candidate["item"]
        return {
            "item_id": item.id,
            "class_id": candidate["item_class"].id,
            "score": candidate["score"],
            "available": candidate["remaining"],
            "reasons": candidate["reasons"],
        }

    def _combine_plan_lines(self, lines: list[EventPlanLine]) -> list[EventPlanLine]:
        combined: dict[tuple[str, ...], EventPlanLine] = {}
        for line in lines:
            key = (
                ("item", line.item_id)
                if line.item_id
                else ("missing", line.capability, line.level)
            )
            existing = combined.get(key)
            if existing is None:
                combined[key] = line
                continue
            existing.amount += line.amount
            existing.available = max(existing.available, line.available)
            existing.missing += line.missing
            existing.required_amount += line.required_amount
            existing.recommended_amount += line.recommended_amount
            existing.optional_amount += line.optional_amount
            existing.required_missing += line.required_missing
            existing.recommended_missing += line.recommended_missing
            existing.optional_missing += line.optional_missing
            existing.level = min(
                (existing.level, line.level),
                key=lambda level: {"required": 0, "recommended": 1, "optional": 2}.get(level, 3),
            )
            existing.conflict = existing.conflict or line.conflict
            existing.substitution = existing.substitution or line.substitution
            existing.score = max(existing.score, line.score)
            existing.reserved_elsewhere = max(
                existing.reserved_elsewhere,
                line.reserved_elsewhere,
            )
            existing.source = ", ".join(
                sorted(set(existing.source.split(", ")).union(line.source.split(", ")))
            )
            existing.reasons = list(dict.fromkeys([*existing.reasons, *line.reasons]))
            known_alternatives = {alternative.get("item_id") for alternative in existing.alternatives}
            existing.alternatives.extend(
                alternative
                for alternative in line.alternatives
                if alternative.get("item_id") not in known_alternatives
            )
            components: dict[tuple[str, str, str], dict[str, Any]] = {}
            for component in [*existing.components, *line.components]:
                component_key = (
                    str(component.get("capability", "")),
                    str(component.get("level", "required")),
                    str(component.get("source", "event")),
                )
                current = components.setdefault(
                    component_key,
                    {
                        "capability": component_key[0],
                        "level": component_key[1],
                        "amount": 0,
                        "missing": 0,
                        "source": component_key[2],
                    },
                )
                current["amount"] += int(component.get("amount", 0))
                current["missing"] += int(component.get("missing", 0))
            existing.components = list(components.values())
        return sorted(
            combined.values(),
            key=lambda line: (
                {"required": 0, "recommended": 1, "optional": 2}.get(line.level, 3),
                line.capability,
                line.item_id,
            ),
        )

    def _transport_plan(
        self,
        lines: list[EventPlanLine],
        context: dict[str, Any],
    ) -> tuple[dict[str, Any], list[CapabilityRequirement]]:
        payload_kg = 0.0
        volume_m3 = 0.0
        allocated_units = 0
        fallback_weight = {
            "pa": 18.0,
            "microphone": 0.4,
            "mixer": 6.0,
            "lighting": 5.0,
            "power": 3.0,
            "rigging": 15.0,
            "video": 8.0,
            "di": 0.6,
            "stand": 2.5,
            "cable": 0.7,
            "case": 14.0,
            "furniture": 6.0,
            "barrier": 12.0,
            "catering": 10.0,
            "display": 8.0,
            "other": 3.0,
        }
        fallback_volume = {
            "pa": 0.12,
            "lighting": 0.04,
            "rigging": 0.10,
            "case": 0.18,
            "furniture": 0.08,
            "barrier": 0.10,
            "catering": 0.12,
            "display": 0.08,
        }

        for line in lines:
            if not line.item_id:
                continue
            item = self.inventory.get_item(line.item_id)
            if item is None or item.type is None or item.type.value == "transport":
                continue
            allocated_units += line.amount
            item_type = item.type.value
            payload_kg += (item.weight_kg or fallback_weight.get(item_type, 3.0)) * line.amount
            volume_m3 += float(
                item.attributes.get(
                    "volume_m3",
                    fallback_volume.get(item_type, 0.025),
                )
            ) * line.amount

        if not allocated_units:
            return {}, []

        event_size = str(context.get("event_size", "medium"))
        needs_cargo = (
            event_size in {"large", "festival"}
            or payload_kg > 180
            or volume_m3 > 1.2
        )
        vehicle_capability = (
            "transport.vehicle.cargo"
            if needs_cargo
            else "transport.vehicle.standard"
        )
        vehicle_label = "Cargo van or larger" if needs_cargo else "Standard estate car or larger"
        cart_count = max(1, ceil(payload_kg / 140))
        if event_size == "festival":
            cart_count = max(cart_count, 3)
        elif event_size == "large":
            cart_count = max(cart_count, 2)

        summary = {
            "payload_kg": round(payload_kg, 1),
            "volume_m3": round(volume_m3, 2),
            "vehicle_capability": vehicle_capability,
            "vehicle_label": vehicle_label,
            "cart_count": cart_count,
            "basis": f"{allocated_units} allocated units across the event plan",
        }
        return summary, [
            CapabilityRequirement(
                vehicle_capability,
                1,
                source="load and transport estimate",
            ),
            CapabilityRequirement(
                "transport.cart",
                cart_count,
                source="load and transport estimate",
            ),
        ]

    def provision_missing_from_presets(self, plan: EventPlan) -> list[EventPlanLine]:
        added_lines = []

        for line in plan.lines:
            if line.missing <= 0 or line.preset_id is None:
                continue

            preset = self.catalog.get(line.preset_id)
            if preset is None:
                continue

            self.inventory.add_from_preset(
                preset,
                item_id=line.item_id,
                amount=line.missing,
            )
            added_lines.append(line)

        return added_lines

    def use_plan(self, plan: EventPlan):
        if not plan.is_ready:
            raise ValueError("Cannot use event plan while required items are missing.")

        for line in plan.lines:
            if line.item_id and line.missing == 0:
                self.inventory.use_item(line.item_id, line.amount)

    def _add_required_item(
        self,
        item_id: str,
        amount: int,
        source: str,
        totals: dict[str, int],
        sources: dict[str, set[str]],
        unresolved_items: set[str],
        path: list[str],
    ):
        item_id = normalize_item_id(item_id)
        if amount <= 0:
            raise ValueError("Event item amount must be greater than zero.")
        if item_id in path:
            cycle = " -> ".join([*path, item_id])
            raise ValueError(f"Requirement cycle detected: {cycle}")

        totals[item_id] = totals.get(item_id, 0) + amount
        sources.setdefault(item_id, set()).add(source)

        template = self._get_template(item_id)
        if template is None:
            unresolved_items.add(item_id)
            return

        for requirement in template.req:
            self._add_required_item(
                item_id=requirement.item_id,
                amount=requirement.amount * amount,
                source=item_id,
                totals=totals,
                sources=sources,
                unresolved_items=unresolved_items,
                path=[*path, item_id],
            )

    def _get_template(self, item_id: str) -> ItemNode | None:
        inventory_item = self.inventory.get_item(item_id)
        if inventory_item is not None:
            return inventory_item

        preset = self.catalog.find_for_item(item_id)
        if preset is None:
            return None

        return preset.create_item()

    def _make_line(
        self,
        item_id: str,
        amount: int,
        sources: set[str],
    ) -> EventPlanLine:
        inventory_item = self.inventory.get_item(item_id)
        preset = self.catalog.find_for_item(item_id)
        available = inventory_item.count if inventory_item else 0
        reserved_elsewhere = self.reserved_counts.get(item_id, 0)
        effective_available = max(available - reserved_elsewhere, 0)
        missing = max(amount - effective_available, 0)

        return EventPlanLine(
            item_id=item_id,
            amount=amount,
            available=available,
            missing=missing,
            source=", ".join(sorted(sources)),
            type=self._line_type(inventory_item, preset),
            preset_id=preset.id if preset else None,
            reserved_elsewhere=reserved_elsewhere,
            conflict=reserved_elsewhere > 0 and amount > effective_available,
        )

    def _line_type(
        self,
        inventory_item: ItemNode | None,
        preset: InventoryPreset | None,
    ) -> str | None:
        item_type = inventory_item.type if inventory_item else preset.type if preset else None
        return item_type.value if item_type else None
