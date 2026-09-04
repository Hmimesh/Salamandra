from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from Item_node import Requirement
from item_classes import CapabilityRequirement


@dataclass
class EventRecord:
    title: str
    description: str
    start_date: str
    start_time: str = "10:00"
    duration_minutes: int = 240
    location: str = ""
    source_type: str = ""
    source_id: str = ""
    status: str = "planning"
    organization_id: str = "salamandra"
    owner_id: str = ""
    assigned_user_ids: list[str] = field(default_factory=list)
    requested_items: list[Requirement] = field(default_factory=list)
    capability_requirements: list[CapabilityRequirement] = field(default_factory=list)
    milestones: list[dict[str, str]] = field(default_factory=list)
    attendee_count: int = 0
    event_size: str = "medium"
    venue_kind: str = ""
    priority_score: int = 50
    plan: dict[str, Any] = field(default_factory=dict)
    checklist: list[dict[str, Any]] = field(default_factory=list)
    return_checklist: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)
    movements: list[dict[str, Any]] = field(default_factory=list)
    sync_status: str = "local"
    google_calendar_payload: dict[str, Any] = field(default_factory=dict)
    version: int = 1
    id: str = field(default_factory=lambda: uuid4().hex)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "start_date": self.start_date,
            "start_time": self.start_time,
            "duration_minutes": self.duration_minutes,
            "location": self.location,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "status": self.status,
            "organization_id": self.organization_id,
            "owner_id": self.owner_id,
            "assigned_user_ids": self.assigned_user_ids,
            "requested_items": [
                requirement.to_dict() for requirement in self.requested_items
            ],
            "capability_requirements": [
                requirement.to_dict()
                for requirement in self.capability_requirements
            ],
            "milestones": self.milestones,
            "attendee_count": self.attendee_count,
            "event_size": self.event_size,
            "venue_kind": self.venue_kind,
            "priority_score": self.priority_score,
            "plan": self.plan,
            "checklist": self.checklist,
            "return_checklist": self.return_checklist,
            "conflicts": self.conflicts,
            "history": self.history,
            "movements": self.movements,
            "sync_status": self.sync_status,
            "google_calendar_payload": self.google_calendar_payload,
            "version": self.version,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EventRecord:
        return cls(
            id=data.get("id", uuid4().hex),
            title=data.get("title", "Untitled Event"),
            description=data.get("description", ""),
            start_date=data.get("start_date", ""),
            start_time=data.get("start_time", "10:00"),
            duration_minutes=int(data.get("duration_minutes", 240)),
            location=data.get("location", ""),
            source_type=data.get("source_type", ""),
            source_id=data.get("source_id", ""),
            status=data.get("status", "planning"),
            organization_id=data.get("organization_id", "salamandra"),
            owner_id=data.get("owner_id", ""),
            assigned_user_ids=list(data.get("assigned_user_ids", [])),
            requested_items=[
                Requirement.from_dict(requirement)
                for requirement in data.get("requested_items", [])
            ],
            capability_requirements=[
                CapabilityRequirement.from_dict(requirement)
                for requirement in data.get("capability_requirements", [])
            ],
            milestones=list(data.get("milestones", [])),
            attendee_count=int(data.get("attendee_count", 0)),
            event_size=data.get("event_size", "medium"),
            venue_kind=data.get("venue_kind", ""),
            priority_score=int(data.get("priority_score", 50)),
            plan=data.get("plan", {}),
            checklist=list(data.get("checklist", [])),
            return_checklist=list(data.get("return_checklist", [])),
            conflicts=list(data.get("conflicts", [])),
            history=list(data.get("history", [])),
            movements=list(data.get("movements", [])),
            sync_status=data.get("sync_status", "local"),
            google_calendar_payload=data.get("google_calendar_payload", {}),
            version=int(data.get("version", 1)),
            created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
        )

    def prepare_operations(self, actor_id: str = ""):
        self.checklist = self._checklist("pack", self.checklist)
        self.return_checklist = self._checklist("return", self.return_checklist)
        self.conflicts = [
            line
            for line in self.plan.get("lines", [])
            if line.get("conflict") or line.get(
                "required_missing",
                line.get("missing", 0) if line.get("level", "required") == "required" else 0,
            ) > 0
        ]
        self.add_history("prepared", actor_id, "Operations checklists created.")

    def add_history(self, action: str, actor_id: str = "", note: str = ""):
        self.history.append(
            {
                "action": action,
                "actor_id": actor_id,
                "note": note,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    def _checklist(
        self,
        phase: str,
        existing_items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if existing_items:
            return existing_items
        return [
            {
                "id": f"{phase}-{index}-{line.get('item_id') or line.get('capability', '')}",
                "item_id": line.get("item_id", ""),
                "capability": line.get("capability", ""),
                "amount": int(line.get("amount", 1)),
                "phase": phase,
                "done": False,
            }
            for index, line in enumerate(self.plan.get("lines", []))
            if line.get("item_id") and line.get("level", "required") != "optional"
        ]


class EventMemory:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.events = self._load()

    def add(self, event: EventRecord):
        self.events[event.id] = event
        self.save()

    def remove(self, event_id: str, organization_id: str) -> bool:
        event = self.get_for_organization(event_id, organization_id)
        if event is None:
            return False
        del self.events[event_id]
        self.save()
        return True

    def get(self, event_id: str) -> EventRecord | None:
        return self.events.get(event_id)

    def get_for_organization(
        self,
        event_id: str,
        organization_id: str,
    ) -> EventRecord | None:
        event = self.get(event_id)
        if event is None or event.organization_id != organization_id:
            return None
        return event

    def get_by_movement_key(
        self,
        idempotency_key: str,
        organization_id: str,
    ) -> EventRecord | None:
        for event in self.events.values():
            if event.organization_id != organization_id:
                continue
            if any(
                movement.get("idempotency_key") == idempotency_key
                for movement in event.movements
            ):
                return event
        return None

    def list_events(self, organization_id: str | None = None) -> list[EventRecord]:
        events = self.events.values()
        if organization_id:
            events = (
                event
                for event in events
                if event.organization_id == organization_id
            )
        return sorted(
            events,
            key=lambda event: (event.start_date, event.start_time, event.title),
        )

    def suggest_from_history(
        self,
        description: str,
        organization_id: str | None = None,
    ) -> list[Requirement]:
        tokens = self._tokens(description)
        suggestions: dict[str, int] = {}

        for event in self.events.values():
            if organization_id and event.organization_id != organization_id:
                continue
            overlap = tokens.intersection(self._tokens(event.description))
            if len(overlap) < 2:
                continue

            for requirement in event.requested_items:
                suggestions[requirement.item_id] = max(
                    suggestions.get(requirement.item_id, 0),
                    requirement.amount,
                )

        return [
            Requirement(item_id=item_id, amount=amount)
            for item_id, amount in sorted(suggestions.items())
        ]

    def to_dict(self, organization_id: str | None = None) -> dict[str, Any]:
        return {
            "events": [
                event.to_dict()
                for event in self.list_events(organization_id)
            ],
            "learning_count": len(self.list_events(organization_id)),
            "active_reservations": self.active_reservations(
                organization_id=organization_id,
            ),
        }

    def active_reservations(
        self,
        exclude_event_id: str | None = None,
        organization_id: str | None = None,
        start_date: str | None = None,
        start_time: str = "00:00",
        duration_minutes: int = 1440,
    ) -> dict[str, int]:
        reservations: dict[str, int] = {}
        active_statuses = {"planning", "confirmed", "packed"}

        for event in self.events.values():
            if event.id == exclude_event_id or event.status not in active_statuses:
                continue
            if organization_id and event.organization_id != organization_id:
                continue
            if start_date and not self._events_overlap(
                event,
                start_date,
                start_time,
                duration_minutes,
            ):
                continue
            for line in event.plan.get("lines", []):
                if line.get("missing", 0) > 0:
                    continue
                item_id = line.get("item_id", "")
                if not item_id:
                    continue
                reservations[item_id] = reservations.get(item_id, 0) + int(line.get("amount", 1))

        return reservations

    def overlapping_events(
        self,
        start_date: str,
        start_time: str,
        duration_minutes: int,
        organization_id: str,
        exclude_event_id: str | None = None,
    ) -> list[EventRecord]:
        active_statuses = {"planning", "confirmed", "packed", "out"}
        return [
            event
            for event in self.list_events(organization_id)
            if event.id != exclude_event_id
            and event.status in active_statuses
            and self._events_overlap(event, start_date, start_time, duration_minutes)
        ]

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as file:
            json.dump(self.to_dict(), file, indent=4)

    def _load(self) -> dict[str, EventRecord]:
        if not self.path.exists():
            return {}

        with open(self.path, "r", encoding="utf-8") as file:
            data = json.load(file)

        return {
            event.id: event
            for event in (
                EventRecord.from_dict(event_data)
                for event_data in data.get("events", [])
            )
        }

    def _tokens(self, description: str) -> set[str]:
        return {
            token.strip(".,:;!?()[]").lower()
            for token in description.split()
            if len(token.strip(".,:;!?()[]")) > 2
        }

    def _events_overlap(
        self,
        event: EventRecord,
        start_date: str,
        start_time: str,
        duration_minutes: int,
    ) -> bool:
        try:
            requested_start = datetime.fromisoformat(f"{start_date}T{start_time}:00")
            requested_end = requested_start + timedelta(minutes=duration_minutes)
            event_start = datetime.fromisoformat(
                f"{event.start_date}T{event.start_time}:00"
            )
            event_end = event_start + timedelta(minutes=event.duration_minutes)
        except ValueError:
            return False
        return requested_start < event_end and event_start < requested_end
