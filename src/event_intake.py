from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from math import ceil
from typing import Any
from uuid import uuid4

from event_memory import EventMemory, EventRecord
from event_planner import EventPlan, EventPlanner
from Inventory import Inventory
from Item_node import Requirement
from item_classes import CapabilityRequirement, ItemClassCatalog
from presets import PresetCatalog


DEFAULT_TIMEZONE = "Asia/Jerusalem"


@dataclass
class EventDraft:
    record: EventRecord
    learned_items: list[Requirement]

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.record.to_dict(),
            "learned_items": [
                requirement.to_dict() for requirement in self.learned_items
            ],
        }


class EventDescriptionPlanner:
    def __init__(
        self,
        inventory: Inventory,
        catalog: PresetCatalog,
        memory: EventMemory,
        reserved_counts: dict[str, int] | None = None,
        organization_id: str = "salamandra",
        item_classes: ItemClassCatalog | None = None,
        exclude_event_id: str | None = None,
        optimize_overlaps: bool = True,
    ):
        self.inventory = inventory
        self.catalog = catalog
        self.memory = memory
        self.reserved_counts = reserved_counts or {}
        self.organization_id = organization_id
        self.item_classes = item_classes or ItemClassCatalog()
        self.exclude_event_id = exclude_event_id
        self.optimize_overlaps = optimize_overlaps

    def draft_from_description(
        self,
        description: str,
        overrides: dict[str, Any] | None = None,
    ) -> EventDraft:
        clean_description = description.strip()
        if not clean_description:
            raise ValueError("Describe the event before generating a plan.")

        overrides = overrides or {}
        start_date = (
            date.fromisoformat(overrides["start_date"])
            if overrides.get("start_date")
            else self._extract_date(clean_description)
        )
        milestones = self._extract_milestones(clean_description)
        start_time = overrides.get("start_time") or (
            milestones[0]["time"] if milestones else self._extract_time(clean_description)
        )
        duration_minutes = int(
            overrides.get("duration_minutes")
            or self._extract_duration(clean_description, milestones)
        )
        attendee_count = (
            int(overrides["attendee_count"])
            if overrides.get("attendee_count") not in (None, "")
            else self._extract_attendees(clean_description.lower())
        )
        if attendee_count < 0 or attendee_count > 1_000_000:
            raise ValueError("Guest count is invalid.")
        event_size = self._extract_event_size(clean_description, attendee_count)
        venue_kind = self._extract_venue_kind(clean_description)
        priority_score = self._priority_score(clean_description, event_size)
        if overrides.get("planning_mode") == "manual":
            capability_requirements = self._manual_capability_requests(
                overrides.get("capability_requirements", [])
            )
        else:
            capability_requirements = self._capability_requests(
                clean_description,
                event_size,
                attendee_count,
                venue_kind,
            )
        requested_items = self._legacy_requests_from_capabilities(capability_requirements)
        learned_items = self.memory.suggest_from_history(
            clean_description,
            organization_id=self.organization_id,
        )
        requested_items = self._merge_requirements([*requested_items, *learned_items])

        event_id = uuid4().hex
        context = {
            "id": event_id,
            "title": overrides.get("title") or self._title_from_description(clean_description),
            "priority_score": priority_score,
            "event_size": event_size,
            "venue_kind": venue_kind,
            "outdoor": venue_kind == "outdoor" or "outdoor" in clean_description.lower(),
            "start_date": start_date.isoformat(),
            "start_time": start_time,
            "duration_minutes": duration_minutes,
            "capability_requirements": [
                requirement.to_dict() for requirement in capability_requirements
            ],
        }
        overlapping_events = self.memory.overlapping_events(
            start_date.isoformat(),
            start_time,
            duration_minutes,
            self.organization_id,
            exclude_event_id=self.exclude_event_id,
        )
        optimizable_events = (
            [event for event in overlapping_events if event.capability_requirements]
            if self.optimize_overlaps
            else []
        )
        legacy_reservations = self._legacy_overlap_reservations(
            overlapping_events,
            include_capability_events=not self.optimize_overlaps,
        )
        planner = EventPlanner(
            self.inventory,
            self.catalog,
            reserved_counts={**self.reserved_counts, **legacy_reservations},
            item_classes=self.item_classes,
            organization_id=self.organization_id,
        )
        allocation_events = [event.to_dict() for event in optimizable_events]
        allocation_events.append(context)
        plans = planner.allocate_overlapping_events(allocation_events)
        plan = plans[event_id]
        self._attach_reallocations(plan, optimizable_events, plans)

        record = EventRecord(
            id=event_id,
            title=context["title"],
            description=clean_description,
            start_date=start_date.isoformat(),
            start_time=start_time,
            duration_minutes=duration_minutes,
            location=overrides.get("location") or self._extract_location(clean_description),
            organization_id=self.organization_id,
            assigned_user_ids=list(overrides.get("assigned_user_ids", [])),
            requested_items=requested_items,
            capability_requirements=capability_requirements,
            milestones=milestones,
            attendee_count=attendee_count,
            event_size=event_size,
            venue_kind=venue_kind,
            priority_score=priority_score,
            plan=plan.to_dict(),
        )
        record.google_calendar_payload = self.google_calendar_payload(record)
        record.prepare_operations()
        return EventDraft(record=record, learned_items=learned_items)

    def _manual_capability_requests(
        self, values: Any
    ) -> list[CapabilityRequirement]:
        if not isinstance(values, list) or not values:
            raise ValueError("Add at least one requirement to the manual event plan.")
        requirements: list[CapabilityRequirement] = []
        for value in values:
            if not isinstance(value, dict):
                raise ValueError("Each event requirement must be an object.")
            capability = str(value.get("capability", "")).strip().lower()
            if not capability or len(capability) > 120 or not re.fullmatch(
                r"[a-z0-9][a-z0-9._-]*", capability
            ):
                raise ValueError("Each manual requirement needs a valid capability.")
            try:
                amount = int(value.get("amount", 0))
            except (TypeError, ValueError) as error:
                raise ValueError("Requirement quantity must be a whole number.") from error
            if amount < 1 or amount > 10_000:
                raise ValueError("Requirement quantity must be between 1 and 10,000.")
            level = str(value.get("level", "required")).strip().lower()
            if level not in {"required", "recommended", "optional"}:
                raise ValueError("Requirement priority is invalid.")
            requirements.append(
                CapabilityRequirement(
                    capability=capability,
                    amount=amount,
                    level=level,
                    source="manual",
                )
            )
        return self._merge_capability_requirements(requirements)

    def google_calendar_payload(self, event: EventRecord) -> dict[str, Any]:
        start = datetime.fromisoformat(f"{event.start_date}T{event.start_time}:00")
        end = start + timedelta(minutes=event.duration_minutes)
        gear_lines = [
            f"- {line.get('amount', 1)}x {line.get('item_id')}"
            for line in event.plan.get("lines", [])
            if line.get("item_id") and line.get("level", "required") == "required"
        ]
        milestone_lines = [
            f"- {milestone['time']} {milestone['label']}"
            for milestone in event.milestones
        ]

        return {
            "calendar_id": "primary",
            "title": event.title,
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "timezone_str": DEFAULT_TIMEZONE,
            "location": event.location,
            "description": "\n".join(
                [
                    event.description,
                    "",
                    "Run of show:",
                    *milestone_lines,
                    "",
                    "Salamandra allocated gear:",
                    *gear_lines,
                ]
            ),
            "attendees": [],
            "add_google_meet": False,
            "transparency": "opaque",
        }

    def _capability_requests(
        self,
        description: str,
        event_size: str,
        attendee_count: int,
        venue_kind: str,
    ) -> list[CapabilityRequirement]:
        text = description.lower()
        requirements: list[CapabilityRequirement] = []
        music_terms = (
            "performance",
            "preformance",
            "singer",
            "band",
            "concert",
            "festival",
            "guitar",
            "saxophone",
            "sax",
            "oud",
            "harmonica",
            "live music",
        )
        speech_terms = ("conference", "presentation", "lecture", "speech", "panel", "talk")
        has_music = any(term in text for term in music_terms)
        has_speech = any(term in text for term in speech_terms)
        no_lighting = bool(
            re.search(r"\b(no|without)\s+(lighting|lights?)\b", text)
            or "lights not needed" in text
        )

        pa_count = 4 if event_size in {"large", "festival"} else 2
        requirements.append(CapabilityRequirement("pa.main", pa_count))

        if has_music:
            monitor_count = {"small": 2, "medium": 4, "large": 6, "festival": 8}[event_size]
            vocal_count = max(1, len(re.findall(r"\bsingers?\b", text)))
            instrument_mic_count = sum(
                1
                for pattern in (
                    r"\bsax(?:ophone)?\b",
                    r"\boud\b",
                    r"\bharmonica\b",
                )
                if re.search(pattern, text)
            )
            instrument_mic_count = max(instrument_mic_count, 0)
            input_count = vocal_count + instrument_mic_count
            di_count = 1 if "guitar" in text or "keyboard" in text else 0
            input_count += di_count
            mixer_channels = max(8, 2 * ceil(max(input_count, 1) / 2))

            requirements.extend(
                [
                    CapabilityRequirement("monitor.stage", monitor_count),
                    CapabilityRequirement(
                        "mixer.audio",
                        1,
                        attributes={
                            "input_channels": mixer_channels,
                            "monitor_sends": min(monitor_count, 4),
                        },
                    ),
                    CapabilityRequirement("microphone.vocal", vocal_count),
                ]
            )
            if instrument_mic_count:
                requirements.append(
                    CapabilityRequirement("microphone.instrument", instrument_mic_count)
                )
            if di_count:
                requirements.append(CapabilityRequirement("di.instrument", di_count))
        elif has_speech:
            requirements.extend(
                [
                    CapabilityRequirement("mixer.audio", 1, attributes={"input_channels": 6}),
                    CapabilityRequirement("microphone.vocal", 2),
                    CapabilityRequirement("monitor.stage", 1),
                ]
            )

        is_outdoor = venue_kind == "outdoor" or "outdoor" in text or "outside" in text
        if is_outdoor:
            requirements.append(CapabilityRequirement("power.distribution", 1))

        lighting_requested = any(
            keyword in text for keyword in ("lighting", "lights", "stage wash", "led", "night")
        )
        if not no_lighting and (lighting_requested or event_size == "festival"):
            light_count = {"small": 4, "medium": 6, "large": 8, "festival": 12}[event_size]
            requirements.append(CapabilityRequirement("lighting.fixture", light_count))

        for item_class in self.item_classes.list_classes(self.organization_id):
            if no_lighting and any(
                capability.startswith("lighting.")
                for capability in item_class.capabilities
            ):
                continue
            terms = [item_class.name, item_class.id.replace("-", " "), *item_class.aliases]
            matched = next(
                (
                    (term, self._plural_term_pattern(term))
                    for term in terms
                    if term and re.search(rf"\b{self._plural_term_pattern(term)}\b", text)
                ),
                None,
            )
            if not matched:
                continue
            matched_term, matched_pattern = matched
            amount_match = re.search(
                rf"\b(\d+)\s*(?:x\s*)?{matched_pattern}\b",
                text,
            )
            requirements.append(
                CapabilityRequirement(
                    item_class.capabilities[0],
                    int(amount_match.group(1)) if amount_match else 1,
                )
            )

        return self._merge_capability_requirements(requirements)

    def _plural_term_pattern(self, term: str) -> str:
        normalized = term.lower().strip()
        if normalized.endswith("y") and len(normalized) > 1:
            return f"{re.escape(normalized[:-1])}(?:y|ies)"
        if normalized.endswith(("s", "x", "z", "ch", "sh")):
            return f"{re.escape(normalized)}(?:es)?"
        return f"{re.escape(normalized)}s?"

    def _merge_capability_requirements(
        self,
        requirements: list[CapabilityRequirement],
    ) -> list[CapabilityRequirement]:
        merged: dict[tuple[str, str], CapabilityRequirement] = {}
        for requirement in requirements:
            key = (requirement.capability, requirement.level)
            existing = merged.get(key)
            if existing is None:
                merged[key] = requirement
                continue
            attributes = dict(existing.attributes)
            for name, value in requirement.attributes.items():
                attributes[name] = max(attributes.get(name, value), value)
            merged[key] = CapabilityRequirement(
                capability=requirement.capability,
                amount=max(existing.amount, requirement.amount),
                level=requirement.level,
                source=existing.source,
                min_quality=max(existing.min_quality, requirement.min_quality),
                required_connectors=tuple(
                    sorted(set(existing.required_connectors).union(requirement.required_connectors))
                ),
                attributes=attributes,
            )
        return sorted(merged.values(), key=lambda requirement: requirement.capability)

    def _legacy_requests_from_capabilities(
        self,
        requirements: list[CapabilityRequirement],
    ) -> list[Requirement]:
        legacy_ids = {
            "pa.main": "pa speaker",
            "monitor.stage": "powered stage monitor",
            "mixer.audio": "small mixer",
            "microphone.vocal": "shure sm58 microphone",
            "microphone.instrument": "shure sm58 microphone",
            "di.instrument": "di box",
            "power.distribution": "power distro 63a",
            "lighting.fixture": "led par light",
        }
        return self._merge_requirements(
            [
                Requirement(legacy_ids[requirement.capability], requirement.amount)
                for requirement in requirements
                if requirement.level == "required" and requirement.capability in legacy_ids
            ]
        )

    def _merge_requirements(self, requirements: list[Requirement]) -> list[Requirement]:
        merged: dict[str, int] = {}
        for requirement in requirements:
            merged[requirement.item_id] = max(
                merged.get(requirement.item_id, 0),
                requirement.amount,
            )
        return [
            Requirement(item_id=item_id, amount=amount)
            for item_id, amount in sorted(merged.items())
        ]

    def _attach_reallocations(
        self,
        plan: EventPlan,
        overlapping_events: list[EventRecord],
        plans: dict[str, EventPlan],
    ):
        for event in overlapping_events:
            optimized = plans[event.id]
            before = self._allocation_counts(event.plan)
            after = self._allocation_counts(optimized.to_dict())
            if before == after:
                continue
            removed = {item_id: amount - after.get(item_id, 0) for item_id, amount in before.items() if amount > after.get(item_id, 0)}
            added = {item_id: amount - before.get(item_id, 0) for item_id, amount in after.items() if amount > before.get(item_id, 0)}
            plan.reallocations.append(
                {
                    "event_id": event.id,
                    "event_title": event.title,
                    "removed": removed,
                    "added": added,
                    "required_missing": optimized.total_missing,
                    "reason": (
                        "Higher-priority overlapping work receives the strongest scarce match; this event remains covered by sufficient substitutes."
                        if optimized.is_ready
                        else f"Higher-priority overlapping work receives the strongest scarce match; the affected event now has {optimized.total_missing} required items to source."
                    ),
                }
            )
            plan.allocation_updates.append(
                {
                    "event_id": event.id,
                    "plan": optimized.to_dict(),
                }
            )

    def _allocation_counts(self, plan: dict[str, Any]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for line in plan.get("lines", []):
            item_id = line.get("item_id", "")
            if item_id and line.get("level", "required") != "optional":
                counts[item_id] = counts.get(item_id, 0) + int(line.get("amount", 1))
        return counts

    def _legacy_overlap_reservations(
        self,
        events: list[EventRecord],
        *,
        include_capability_events: bool = False,
    ) -> dict[str, int]:
        reservations: dict[str, int] = {}
        for event in events:
            if event.capability_requirements and not include_capability_events:
                continue
            for item_id, amount in self._allocation_counts(event.plan).items():
                reservations[item_id] = reservations.get(item_id, 0) + amount
        return reservations

    def _title_from_description(self, description: str) -> str:
        first_sentence = re.split(r"[.!?\n]", description, maxsplit=1)[0].strip()
        if len(first_sentence) > 64:
            first_sentence = first_sentence[:61].rstrip() + "..."
        return first_sentence or "Untitled Event"

    def _extract_date(self, description: str) -> date:
        iso_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", description)
        if iso_match:
            return date.fromisoformat(iso_match.group(1))

        numeric_match = re.search(
            r"\b(0?[1-9]|[12]\d|3[01])[./](0?[1-9]|1[0-2])(?:[./](20\d{2}|\d{2}))?\b",
            description,
        )
        if numeric_match:
            day = int(numeric_match.group(1))
            month = int(numeric_match.group(2))
            raw_year = numeric_match.group(3)
            year = int(raw_year) if raw_year else date.today().year
            if year < 100:
                year += 2000
            return date(year, month, day)

        text = description.lower()
        month_names = {
            month.lower(): index
            for index, month in enumerate(
                ("", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
            )
            if month
        }
        for month_name, month_number in month_names.items():
            match = re.search(rf"\b{month_name}\s+(\d{{1,2}})(?:,?\s+(20\d{{2}}))?\b", text)
            if match:
                return date(int(match.group(2) or date.today().year), month_number, int(match.group(1)))
        for month_name, month_number in month_names.items():
            if month_name in text:
                today = date.today()
                return date(today.year, month_number, min(today.day, 28))

        today = date.today()
        if "tomorrow" in text:
            return today + timedelta(days=1)
        if "next week" in text:
            return today + timedelta(days=7)
        return today + timedelta(days=14)

    def _extract_milestones(self, description: str) -> list[dict[str, str]]:
        milestones = []
        for match in re.finditer(
            r"(?m)^\s*([01]?\d|2[0-3]):([0-5]\d)\s*(?:[-:–]\s*)?([^\n,.;]+)",
            description,
        ):
            raw_label = match.group(3).strip()
            if not raw_label:
                continue
            label = self._normalize_milestone_label(raw_label)
            milestones.append(
                {
                    "time": f"{int(match.group(1)):02d}:{match.group(2)}",
                    "label": label,
                }
            )
        unique = {(item["time"], item["label"]): item for item in milestones}
        return sorted(unique.values(), key=lambda item: item["time"])

    def _normalize_milestone_label(self, label: str) -> str:
        text = label.lower().strip(" -")
        if "set" in text or "load" in text:
            return "Settling"
        if "balance" in text or "soundcheck" in text or "sound check" in text:
            return "Balance"
        if "door" in text:
            return "Doors open"
        if "show" in text or "performance" in text:
            return "Show starts"
        return text.capitalize()

    def _extract_time(self, description: str) -> str:
        time_match = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", description)
        if time_match:
            return f"{int(time_match.group(1)):02d}:{time_match.group(2)}"
        hour_match = re.search(r"\b([1-9]|1[0-2])\s*(am|pm)\b", description.lower())
        if hour_match:
            hour = int(hour_match.group(1))
            if hour_match.group(2) == "pm" and hour != 12:
                hour += 12
            if hour_match.group(2) == "am" and hour == 12:
                hour = 0
            return f"{hour:02d}:00"
        return "10:00"

    def _extract_duration(
        self,
        description: str,
        milestones: list[dict[str, str]],
    ) -> int:
        duration_match = re.search(r"\b(\d+)\s*(hour|hours|hr|hrs)\b", description.lower())
        if duration_match:
            return int(duration_match.group(1)) * 60
        if len(milestones) >= 2:
            start = datetime.strptime(milestones[0]["time"], "%H:%M")
            end = datetime.strptime(milestones[-1]["time"], "%H:%M")
            return max(240, int((end - start).total_seconds() / 60) + 120)
        return 240

    def _extract_location(self, description: str) -> str:
        text = description.lower()
        if "coffee house" in text or "cofee house" in text or "coffee shop" in text:
            return "Coffee house"
        location_match = re.search(r"\bat ([A-Za-z][A-Za-z0-9 \-']+)", description)
        if location_match:
            return location_match.group(1).strip().rstrip(".")
        if "outdoor" in text or "outside" in text:
            return "Outdoor venue"
        return ""

    def _extract_venue_kind(self, description: str) -> str:
        text = description.lower()
        if any(term in text for term in ("coffee house", "cofee house", "coffee shop", "cafe")):
            return "coffee_house"
        if "outdoor" in text or "outside" in text or "festival" in text:
            return "outdoor"
        if "house" in text or "home" in text:
            return "house"
        if "conference" in text or "hall" in text:
            return "hall"
        return ""

    def _extract_event_size(self, description: str, attendee_count: int) -> str:
        text = description.lower()
        if "festival" in text:
            return "festival"
        for size in ("large", "medium", "small"):
            if re.search(rf"\b{size}\b", text):
                return size
        if attendee_count >= 1000:
            return "festival"
        if attendee_count >= 500:
            return "large"
        if attendee_count >= 100:
            return "medium"
        return "small"

    def _priority_score(self, description: str, event_size: str) -> int:
        score = {"small": 35, "medium": 55, "large": 78, "festival": 95}[event_size]
        text = description.lower()
        if "outdoor" in text or "outside" in text:
            score += 2
        if any(term in text for term in ("live", "concert", "performance", "preformance")):
            score += 2
        return min(score, 100)

    def _extract_attendees(self, text: str) -> int:
        attendee_match = re.search(r"(?:around\s+|about\s+)?(\d+)\s*(attendees|people|guests|pax)", text)
        if attendee_match:
            return int(attendee_match.group(1))
        return 0
