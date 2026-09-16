"""Logistics scheduling never changes the actual event duration or stock buckets."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from database import AuditEventModel, FieldAdjustmentModel, active_membership, utc_now
from equipment_conditions import inventory_label
from event_returns import EventReturns
from security import Permission, StateConflict, require_permission
from suggestion_outcomes import receipt, complete, identifier

ZONE = "Asia/Jerusalem"
MILESTONES = ("prepare_at", "pack_by", "standby_at", "dispatch_at", "load_in_at", "setup_at", "teardown_at", "return_due_at")


def instant(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 40:
        raise ValueError("A valid logistics date and time is required.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("A valid logistics date and time is required.") from None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc)
    zone = ZoneInfo(ZONE)
    candidates = {parsed.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc) for fold in (0, 1)
        if parsed.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == parsed}
    if len(candidates) != 1:
        raise ValueError("This local time is ambiguous or nonexistent. Supply an explicit UTC offset.")
    return candidates.pop()


def logistics_window(data):
    start = instant(f"{data.get('start_date', '')}T{data.get('start_time', '10:00')}")
    end = start + timedelta(minutes=int(data.get("duration_minutes", 240)))
    milestones = (data.get("logistics") or {}).get("milestones", {})
    values = {key: instant(value) for key, value in milestones.items()}
    ordered = [values[key] for key in MILESTONES[:6] if key in values] + [start, end] + [values[key] for key in MILESTONES[6:] if key in values]
    if any(left > right for left, right in zip(ordered, ordered[1:])):
        raise ValueError("Logistics milestones must follow operational order around the actual show.")
    return min([start, *[values[key] for key in MILESTONES[:6] if key in values]]), max([end, *[values[key] for key in MILESTONES[6:] if key in values]])


class EventLogistics:
    def __init__(self, factory):
        self.factory = factory

    @staticmethod
    def public(event):
        data = event.data or {}
        logistics = deepcopy(data.get("logistics") or {})
        window = logistics_window(data)
        return {"event_id": event.id, "event_version": event.version, "timezone": ZONE,
            "milestones": logistics.get("milestones", {}), "notes": logistics.get("notes", ""),
            "standby": bool(logistics.get("standby") and event.status == "packed"),
            "reservation_start": window[0].isoformat(), "reservation_end": window[1].isoformat(),
            "event_start": f"{data.get('start_date', '')}T{data.get('start_time', '')}",
            "duration_minutes": data.get("duration_minutes", 240)}

    def get(self, org, actor, event_id):
        with self.factory() as session:
            require_permission(active_membership(session, org, actor).role, Permission.STATE_READ)
            return self.public(EventReturns._event(session, org, identifier(event_id)))

    def export(self, org, actor, event_id):
        with self.factory() as session:
            require_permission(active_membership(session, org, actor).role, Permission.STATE_READ)
            event = EventReturns._event(session, org, identifier(event_id))
            public = self.public(event)
            rows = [["Section", "Name", "Value"], ["Event", "Title", event.title],
                ["Event", "Venue", (event.data or {}).get("location", "")],
                ["Event", "Show start", public["event_start"]], ["Event", "Timezone", ZONE],
                ["Event", "Duration minutes", public["duration_minutes"]]]
            rows.extend(["Logistics", key, value] for key, value in public["milestones"].items())
            rows.append(["Logistics", "Notes", public["notes"]])
            from database import CrewAssignmentModel, CrewProfileModel, CrewRoleModel
            for assignment, profile, role in session.execute(select(CrewAssignmentModel, CrewProfileModel, CrewRoleModel)
                    .join(CrewProfileModel, (CrewProfileModel.id == CrewAssignmentModel.profile_id) & (CrewProfileModel.organization_id == org))
                    .join(CrewRoleModel, (CrewRoleModel.id == CrewAssignmentModel.role_id) & (CrewRoleModel.organization_id == org))
                    .where(CrewAssignmentModel.organization_id == org, CrewAssignmentModel.event_id == event.id,
                        CrewAssignmentModel.status == "assigned").order_by(CrewAssignmentModel.call_at, CrewAssignmentModel.id)):
                rows.append(["Crew", role.name, profile.name])
                rows.append(["Crew call", profile.name, assignment.call_at.isoformat()])
                rows.append(["Crew release", profile.name, assignment.release_at.isoformat()])
            rows.extend(["Equipment", inventory_label(holding), line.quantity] for line, holding in EventReturns._lines(session, org, event.id))
            for adjustment in session.scalars(select(FieldAdjustmentModel).where(FieldAdjustmentModel.organization_id == org,
                    FieldAdjustmentModel.event_id == event.id).order_by(FieldAdjustmentModel.created_at, FieldAdjustmentModel.id)):
                rows.append(["Change", adjustment.status, adjustment.reason])
                rows.extend(["Addition", req["capability"], req["amount"]] for req in adjustment.data.get("requirements", []))
            return rows

    def update(self, org, actor, body, request_id, *, staging=False):
        allowed = {"event_id", "version", "idempotency_key", "standby"} if staging else {"event_id", "version", "idempotency_key", "milestones", "notes"}
        if set(body) - allowed:
            raise ValueError("Unsupported logistics fields.")
        if staging:
            if type(body.get("standby")) is not bool:
                raise ValueError("Choose whether the packed equipment is staged.")
        else:
            milestones = body.get("milestones", {})
            if not isinstance(milestones, dict) or set(milestones) - set(MILESTONES):
                raise ValueError("Unsupported logistics milestones.")
            milestones = {key: instant(value).isoformat() for key, value in milestones.items() if value not in (None, "")}
            notes = body.get("notes", "")
            if not isinstance(notes, str) or len(notes) > 4000 or "\x00" in notes:
                raise ValueError("Operational notes must be at most 4000 characters.")
        with self.factory.begin() as session:
            member = active_membership(session, org, actor)
            require_permission(member.role, Permission.OPERATIONS_PACK if staging else Permission.LOGISTICS_EDIT)
            operation = receipt(session, org, actor, "logistics.stage" if staging else "logistics.update", body.get("idempotency_key"), body)
            if operation.status == "completed":
                return deepcopy(operation.response)
            event = EventReturns._event(session, org, identifier(body.get("event_id")), lock=True)
            if type(body.get("version")) is not int or event.version != body["version"] or event.status in {"returned", "cancelled"}:
                raise StateConflict("The event changed or is closed. Reload its logistics.")
            data = deepcopy(event.data or {})
            previous = deepcopy(data.get("logistics") or {})
            logistics = deepcopy(previous)
            if staging:
                if event.status != "packed":
                    raise StateConflict("Only packed equipment can enter or leave standby.")
                logistics.update({"standby": body["standby"], "staged_at": utc_now().isoformat() if body["standby"] else None})
            else:
                frozen = set(MILESTONES[:2]) if event.status in {"packed", "out"} else set()
                if previous.get("standby") or event.status == "out":
                    frozen.add("standby_at")
                if event.status == "out":
                    frozen.add("dispatch_at")
                if any(milestones.get(key) != previous.get("milestones", {}).get(key) for key in frozen):
                    raise StateConflict("Completed logistics milestones cannot be rewritten.")
                logistics.update({"milestones": milestones, "notes": notes})
            data["logistics"] = logistics
            event.reservation_starts_at, event.reservation_ends_at = logistics_window(data)
            event.version += 1
            data["history"] = [*data.get("history", []), {"action": "logistics.staged" if staging else "logistics.updated",
                "actor_id": actor, "timestamp": utc_now().isoformat(), "note": "Logistics updated."}]
            event.data = data
            session.add(AuditEventModel(organization_id=org, actor_membership_id=member.id, action="logistics.stage" if staging else "logistics.update",
                resource_type="event", resource_id=event.id, request_id=request_id, changes={"before": previous, "after": logistics, "version": event.version}))
            return complete(operation, self.public(event))
