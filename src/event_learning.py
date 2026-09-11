from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.orm import Session, sessionmaker

from database import (
    AllocationLineModel,
    AllocationModel,
    AuditEventModel,
    EventFeedbackItemModel,
    EventFeedbackModel,
    EventLearningRecordModel,
    EventModel,
    OperationRequestModel,
    StockMovementModel,
    new_id,
    utc_now,
)
from security import Permission, ResourceNotFound, StateConflict, require_permission


YES_NO = {"yes", "no"}
PLAN_FIT = {"too_little", "about_right", "too_much"}
REUSE = {"yes", "with_changes", "no"}
ITEM_KINDS = {"missing", "unnecessary", "failed", "additional_onsite"}


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _features(data: dict[str, Any]) -> dict[str, Any]:
    capabilities = data.get("capability_requirements") or []
    codes = [str(item.get("capability", "")) for item in capabilities if isinstance(item, dict)]
    departments = sorted({code.split(".", 1)[0] for code in codes if "." in code})
    def required(prefix: str) -> bool | None:
        matching = [code for code in codes if code.startswith(prefix)]
        return True if matching else None
    return {
        "event_type": data.get("event_type") or None,
        "guest_count": data.get("attendee_count") if data.get("attendee_count") not in (None, "") else None,
        "venue_type": data.get("venue_kind") or None,
        "indoor_outdoor": data.get("indoor_outdoor") or None,
        "duration_minutes": data.get("duration_minutes") if data.get("duration_minutes") not in (None, "") else None,
        "performer_count": data.get("performer_count") if data.get("performer_count") not in (None, "") else None,
        "performer_types": data.get("performer_types") or [],
        "departments": departments,
        "custom_tags": list(data.get("custom_tags") or []),
        "audio_required": required("pa."),
        "lighting_required": required("lighting."),
        "video_required": required("video."),
        "furniture_required": required("furniture."),
        "transport_required": required("transport."),
        "power_required": required("power."),
        "hospitality_required": required("hospitality."),
        "site_infrastructure_required": required("site."),
    }


def _proposal(data: dict[str, Any]) -> dict[str, Any]:
    plan = data.get("plan") or {}
    return {
        "requirements": data.get("capability_requirements") or [],
        "requested_items": data.get("requested_items") or [],
        "lines": plan.get("lines") or [],
        "substitutions": [line for line in plan.get("lines", []) if isinstance(line, dict) and line.get("substitution")],
        "shortages": [line for line in plan.get("lines", []) if isinstance(line, dict) and (line.get("missing") or line.get("required_missing"))],
    }


def _original(data: dict[str, Any], request_payload: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "brief": data.get("description", ""),
        "request": request_payload or {},
        "title": data.get("title", ""),
        "start_date": data.get("start_date", ""),
        "start_time": data.get("start_time", ""),
        "location": data.get("location", ""),
        "features": _features(data),
        "milestones": data.get("milestones") or [],
    }


def _corrections(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    old = _proposal(previous)
    new = _proposal(current)
    return {
        "requirements": {"proposed": old["requirements"], "final_planned": new["requirements"]},
        "lines": {"proposed": old["lines"], "final_planned": new["lines"]},
        "changed": old != new,
    }


class EventLearningStore:
    def __init__(self, factory: sessionmaker[Session]):
        self.factory = factory

    @staticmethod
    def create_in_session(session: Session, event: EventModel, request_payload: dict[str, Any] | None = None, source_type: str | None = None) -> EventLearningRecordModel:
        data = dict(event.data or {})
        row = EventLearningRecordModel(
            organization_id=event.organization_id,
            event_id=event.id,
            source_type=str(source_type or data.get("source_type") or "real"),
            source_id=str(data.get("source_id") or ""),
            eligible=False,
            exclusion_reason="awaiting_return",
            features=_features(data),
            original_request=_original(data, request_payload),
            proposal=_proposal(data),
            corrections={"changed": False, "requirements": {}, "lines": {}},
            execution={},
            version=1,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        session.add(row)
        return row

    @staticmethod
    def sync_execution_in_session(session: Session, event: EventModel) -> None:
        row = session.scalar(select(EventLearningRecordModel).where(EventLearningRecordModel.organization_id == event.organization_id, EventLearningRecordModel.event_id == event.id).with_for_update())
        if row is None:
            row = EventLearningStore.create_in_session(session, event, source_type=str((event.data or {}).get("source_type") or "unknown"))
        movements = session.scalars(select(StockMovementModel).where(StockMovementModel.organization_id == event.organization_id, StockMovementModel.event_id == event.id).order_by(StockMovementModel.created_at, StockMovementModel.id))
        row.execution = {
            "status": event.status,
            "packed": [m.lines for m in movements if m.action == "packed"],
            "dispatched": [m.lines for m in movements if m.action == "out"],
            "returned": [m.lines for m in movements if m.action == "returned"],
        }
        if event.status == "returned" and row.source_type == "real" and row.exclusion_reason == "awaiting_return":
            row.eligible = True
            row.exclusion_reason = None
        row.version += 1

    @staticmethod
    def capture_edit_in_session(session: Session, event: EventModel, previous_data: dict[str, Any]) -> None:
        row = session.scalar(select(EventLearningRecordModel).where(EventLearningRecordModel.organization_id == event.organization_id, EventLearningRecordModel.event_id == event.id).with_for_update())
        if row is not None:
            row.corrections = _corrections(previous_data, dict(event.data or {}))
            row.version += 1

    def get(self, organization_id: str, event_id: str) -> dict[str, Any]:
        with self.factory() as session:
            event = session.scalar(select(EventModel).where(EventModel.organization_id == organization_id, EventModel.id == event_id))
            if event is None:
                raise ResourceNotFound("Event was not found.")
            row = session.scalar(select(EventLearningRecordModel).where(EventLearningRecordModel.organization_id == organization_id, EventLearningRecordModel.event_id == event_id))
            if row is None:
                return {"event_id": event_id, "available": False, "status": event.status}
            feedback = session.scalar(select(EventFeedbackModel).where(EventFeedbackModel.organization_id == organization_id, EventFeedbackModel.event_id == event_id))
            items = []
            if feedback:
                items = [self._item(item) for item in session.scalars(select(EventFeedbackItemModel).where(EventFeedbackItemModel.organization_id == organization_id, EventFeedbackItemModel.feedback_id == feedback.id).order_by(EventFeedbackItemModel.id))]
            return self._record(row, feedback, items)

    def examples(self, organization_id: str, limit: int = 100) -> list[dict[str, Any]]:
        if not organization_id:
            raise ValueError("Organization context is required.")
        with self.factory() as session:
            # Limit candidates before joining item rows. One statement provides a
            # committed snapshot of eligibility, review answers and affected items.
            candidates = select(EventLearningRecordModel.id).join(
                EventModel, and_(EventModel.id == EventLearningRecordModel.event_id,
                                 EventModel.organization_id == organization_id)
            ).where(
                EventLearningRecordModel.organization_id == organization_id,
                EventLearningRecordModel.eligible.is_(True),
                EventLearningRecordModel.source_type == "real",
                EventLearningRecordModel.exclusion_reason.is_(None),
                EventModel.status == "returned",
            ).order_by(EventLearningRecordModel.event_id).limit(max(1, min(limit, 100))).subquery()
            rows = session.execute(select(EventLearningRecordModel, EventFeedbackModel, EventFeedbackItemModel)
                .join(candidates, candidates.c.id == EventLearningRecordModel.id)
                .outerjoin(EventFeedbackModel, and_(EventFeedbackModel.event_id == EventLearningRecordModel.event_id,
                                                   EventFeedbackModel.organization_id == organization_id))
                .outerjoin(EventFeedbackItemModel, and_(EventFeedbackItemModel.feedback_id == EventFeedbackModel.id,
                                                       EventFeedbackItemModel.organization_id == organization_id))
                .where(EventLearningRecordModel.organization_id == organization_id)
                .order_by(EventLearningRecordModel.event_id, EventFeedbackItemModel.id))
            result = {}
            for row, feedback, item in rows:
                if row.event_id not in result:
                    result[row.event_id] = self._record(row, feedback, [])
                if item is not None:
                    result[row.event_id]["feedback"]["items"].append(self._item(item))
            return list(result.values())

    def save_feedback(self, organization_id: str, actor_user_id: str, event_id: str, payload: dict[str, Any], request_id: str, idempotency_key: str, expected_version: int | None) -> dict[str, Any]:
        with self.factory.begin() as session:
            from database import active_membership
            membership = active_membership(session, organization_id, actor_user_id)
            require_permission(membership.role, Permission.EVENTS_FEEDBACK_WRITE)
            event = session.scalar(select(EventModel).where(EventModel.organization_id == organization_id, EventModel.id == event_id).with_for_update())
            if event is None:
                raise ResourceNotFound("Event was not found.")
            if event.status != "returned":
                raise StateConflict("Event feedback is available after the event is returned.")
            fingerprint = _fingerprint({"event_id": event_id, **payload, "expected_version": expected_version})
            op = session.scalar(select(OperationRequestModel).where(OperationRequestModel.organization_id == organization_id, OperationRequestModel.operation == "event.feedback", OperationRequestModel.idempotency_key == idempotency_key).with_for_update())
            if op:
                if op.request_fingerprint != fingerprint:
                    raise StateConflict("This idempotency key was already used for another review.")
                if not op.resource_id:
                    raise StateConflict("This review is still being processed.")
                return self._feedback_response(session, organization_id, event_id)
            if expected_version is not None and event.version != expected_version:
                raise StateConflict("This event changed after you opened it. Reload and review the latest version.")
            values = self._validate_payload(payload)
            items = self._validate_items(event, payload.get("items", []), values)
            feedback = session.scalar(select(EventFeedbackModel).where(EventFeedbackModel.organization_id == organization_id, EventFeedbackModel.event_id == event_id).with_for_update())
            if feedback is not None and (not isinstance(payload.get("feedback_version"), int) or feedback.version != payload["feedback_version"]):
                raise StateConflict("This review changed after you opened it. Reload before saving.")
            if feedback is None:
                feedback = EventFeedbackModel(organization_id=organization_id, event_id=event_id, id=new_id(), **values, version=1, created_at=utc_now(), updated_at=utc_now())
                session.add(feedback)
                session.flush()
            else:
                for key, value in values.items():
                    setattr(feedback, key, value)
                feedback.version += 1
                session.execute(select(EventFeedbackItemModel).where(EventFeedbackItemModel.organization_id == organization_id, EventFeedbackItemModel.feedback_id == feedback.id).with_for_update())
                session.query(EventFeedbackItemModel).filter(EventFeedbackItemModel.organization_id == organization_id, EventFeedbackItemModel.feedback_id == feedback.id).delete(synchronize_session=False)
            for item in items:
                session.add(EventFeedbackItemModel(organization_id=organization_id, feedback_id=feedback.id, kind=item["kind"], item_id=item.get("item_id"), label_snapshot=item["label_snapshot"], quantity=item.get("quantity"), note=item.get("note", "")))
            op = OperationRequestModel(organization_id=organization_id, operation="event.feedback", idempotency_key=idempotency_key, request_fingerprint=fingerprint, status="completed", resource_id=feedback.id, response={"feedback_id": feedback.id}, completed_at=utc_now())
            session.add(op)
            session.add(AuditEventModel(organization_id=organization_id, actor_membership_id=membership.id, action="event.feedback.saved", resource_type="event", resource_id=event_id, request_id=request_id, changes={"feedback_version": feedback.version}))
            return self._feedback_response(session, organization_id, event_id)

    def set_eligibility(self, organization_id: str, actor_user_id: str, event_id: str, eligible: bool, reason: str, request_id: str, expected_version: int | None, idempotency_key: str) -> dict[str, Any]:
        with self.factory.begin() as session:
            from database import active_membership
            membership = active_membership(session, organization_id, actor_user_id)
            require_permission(membership.role, Permission.EVENTS_LEARNING_MANAGE)
            event = session.scalar(select(EventModel).where(EventModel.organization_id == organization_id, EventModel.id == event_id).with_for_update())
            if event is None:
                raise ResourceNotFound("Event was not found.")
            if expected_version is not None and event.version != expected_version:
                raise StateConflict("This event changed after you opened it. Reload before changing history eligibility.")
            payload = {"event_id": event_id, "eligible": eligible, "reason": reason, "expected_version": expected_version}
            fingerprint = _fingerprint(payload)
            operation = session.scalar(select(OperationRequestModel).where(OperationRequestModel.organization_id == organization_id, OperationRequestModel.operation == "event.learning.eligibility", OperationRequestModel.idempotency_key == idempotency_key).with_for_update())
            if operation:
                if operation.request_fingerprint != fingerprint:
                    raise StateConflict("This idempotency key was already used for another history change.")
                row = session.scalar(select(EventLearningRecordModel).where(EventLearningRecordModel.organization_id == organization_id, EventLearningRecordModel.event_id == event_id))
                if row is None:
                    raise StateConflict("The completed history record is unavailable.")
                return self._record(row, None, [])
            row = session.scalar(select(EventLearningRecordModel).where(EventLearningRecordModel.organization_id == organization_id, EventLearningRecordModel.event_id == event_id).with_for_update())
            if row is None:
                row = self.create_in_session(session, event, source_type=str((event.data or {}).get("source_type") or "unknown"))
            if eligible and event.status != "returned":
                raise StateConflict("Only returned events can be included in planning history.")
            if eligible and row.source_type != "real":
                raise StateConflict("This event source is excluded from planning history.")
            row.eligible = bool(eligible)
            row.exclusion_reason = None if eligible else (reason.strip()[:80] or "excluded_by_owner")
            row.version += 1
            session.add(OperationRequestModel(organization_id=organization_id, operation="event.learning.eligibility", idempotency_key=idempotency_key, request_fingerprint=fingerprint, status="completed", resource_id=row.id, response={"event_id": event_id}, completed_at=utc_now()))
            session.add(AuditEventModel(organization_id=organization_id, actor_membership_id=membership.id, action="event.learning_eligibility.changed", resource_type="event", resource_id=event_id, request_id=request_id, changes={"eligible": bool(eligible), "version": row.version}))
            return self._record(row, None, [])

    @staticmethod
    def _validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
        values = {}
        for key in ("missing", "unnecessary", "failed", "additional_onsite"):
            if payload.get(key) not in YES_NO:
                raise ValueError(f"Feedback field {key} must be yes or no.")
            values[key] = payload[key]
        for key, allowed in (("plan_fit", PLAN_FIT), ("reuse_plan", REUSE)):
            if payload.get(key) not in allowed:
                raise ValueError(f"Feedback field {key} is invalid.")
            values[key] = payload[key]
        notes = payload.get("notes", "")
        if not isinstance(notes, str) or len(notes) > 2000:
            raise ValueError("Feedback notes are invalid.")
        values["notes"] = notes
        items = payload.get("items", [])
        if not isinstance(items, list) or len(items) > 100:
            raise ValueError("Feedback items are invalid.")
        for item in items:
            if not isinstance(item, dict) or item.get("kind") not in ITEM_KINDS:
                raise ValueError("Feedback item type is invalid.")
            if item.get("quantity") is not None and (not isinstance(item["quantity"], int) or item["quantity"] < 1):
                raise ValueError("Feedback item quantity is invalid.")
            for key in ("label_snapshot", "note"):
                if not isinstance(item.get(key, ""), str) or len(item.get(key, "")) > (300 if key == "label_snapshot" else 500):
                    raise ValueError("Feedback item text is invalid.")
        return values

    @staticmethod
    def _validate_items(event: EventModel, items: list[Any], values: dict[str, Any]) -> list[dict[str, Any]]:
        allowed = {str(line.get("item_id")) for line in (event.data or {}).get("plan", {}).get("lines", []) if isinstance(line, dict) and line.get("item_id")}
        answer_for = {"missing": values["missing"], "unnecessary": values["unnecessary"], "failed": values["failed"], "additional_onsite": values["additional_onsite"]}
        validated = []
        for item in items:
            if item["kind"] in answer_for and answer_for[item["kind"]] != "yes":
                raise ValueError("An affected item needs a yes answer for its category.")
            item_id = item.get("item_id")
            if item_id is not None:
                if not isinstance(item_id, str) or item_id not in allowed:
                    raise ResourceNotFound("The affected item was not part of this event.")
                label = item_id
            else:
                label = item.get("label_snapshot", "")
            validated.append({**item, "item_id": item_id, "label_snapshot": label})
        return validated

    @staticmethod
    def _item(item: EventFeedbackItemModel) -> dict[str, Any]:
        return {"kind": item.kind, "item_id": item.item_id, "label_snapshot": item.label_snapshot, "quantity": item.quantity, "note": item.note}

    def _feedback_response(self, session: Session, organization_id: str, event_id: str) -> dict[str, Any]:
        feedback = session.scalar(select(EventFeedbackModel).where(EventFeedbackModel.organization_id == organization_id, EventFeedbackModel.event_id == event_id))
        items = [self._item(item) for item in session.scalars(select(EventFeedbackItemModel).where(EventFeedbackItemModel.organization_id == organization_id, EventFeedbackItemModel.feedback_id == feedback.id))] if feedback else []
        return {"feedback": {"id": feedback.id, "version": feedback.version, **{key: getattr(feedback, key) for key in ("missing", "unnecessary", "failed", "additional_onsite", "plan_fit", "reuse_plan", "notes")}, "items": items} if feedback else None}

    @staticmethod
    def _record(row: EventLearningRecordModel, feedback: EventFeedbackModel | None, items: list[dict[str, Any]]) -> dict[str, Any]:
        return {"available": True, "id": row.id, "event_id": row.event_id, "source_type": row.source_type, "source_id": row.source_id, "eligible": row.eligible, "exclusion_reason": row.exclusion_reason, "features": row.features, "original_request": row.original_request, "proposal": row.proposal, "corrections": row.corrections, "execution": row.execution, "feedback": ({"id": feedback.id, "version": feedback.version, **{key: getattr(feedback, key) for key in ("missing", "unnecessary", "failed", "additional_onsite", "plan_fit", "reuse_plan", "notes")}, "items": items} if feedback else None), "version": row.version}
