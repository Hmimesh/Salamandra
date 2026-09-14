"""Minimal suggestion evidence. No allocation or inventory mutation lives here."""
from copy import deepcopy
from contextlib import nullcontext
import re

from sqlalchemy import case, func, select, true
from sqlalchemy.exc import IntegrityError

from database import (AuditEventModel, EventModel, EventLearningRecordModel, EventFeedbackModel,
                      EventFeedbackItemModel, OperationRequestModel, SuggestionSessionModel,
                      SuggestionEvaluationModel, active_membership, new_id, utc_now)
from event_learning import EventLearningStore, _fingerprint
from event_similarity import EventSimilarityService, evidence_quantities, final_requirements, validate_features
from security import Permission, ResourceNotFound, StateConflict, require_permission

ALGORITHM_VERSION = "D1"
RETRIEVAL_VERSION = "E1"


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", value):
        raise ValueError("Invalid suggestion identifier.")
    return value


def receipt(session, org, actor, operation, key, payload):
    if not isinstance(key, str) or not 1 <= len(key.strip()) <= 160:
        raise ValueError("An idempotency key is required.")
    fingerprint = _fingerprint({"actor": actor, "payload": payload})
    connection = session.connection()
    if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
        # sqlite3 legacy transaction control otherwise commits a first savepoint
        # outside the surrounding transaction, preventing rollback on validation.
        connection.exec_driver_sql("BEGIN")
    # A unique insert acquires just this command key, including across processes.
    try:
        with session.begin_nested():
            row = OperationRequestModel(organization_id=org, operation=operation,
                idempotency_key=key, request_fingerprint=fingerprint)
            session.add(row)
            session.flush()
    except IntegrityError:
        row = session.scalar(select(OperationRequestModel).where(
            OperationRequestModel.organization_id == org, OperationRequestModel.operation == operation,
            OperationRequestModel.idempotency_key == key).with_for_update())
        if row is None or row.request_fingerprint != fingerprint:
            raise StateConflict("This request key was already used for another command.") from None
    return row


def complete(receipt_row, response):
    receipt_row.status = "completed"
    receipt_row.response = deepcopy(response)
    receipt_row.completed_at = utc_now()
    return response


def public_session(row):
    return {"session_id": row.id, "event_id": row.event_id_snapshot, "event_version": row.event_version,
            "algorithm_version": row.algorithm_version, "retrieval_version": row.retrieval_version,
            **deepcopy(row.snapshot), "action": row.action, "applied": row.applied}


class SuggestionOutcomes:
    def __init__(self, factory):
        self.factory = factory

    def generate(self, org, actor, body, request_id):
        if set(body) - {"features", "provenance", "event_id", "event_version", "idempotency_key"}:
            raise ValueError("Unsupported suggestion-session fields.")
        features = validate_features(body.get("features"))
        provenance = body.get("provenance", {})
        if (not isinstance(provenance, dict)
                or set(provenance) - {"departments", "guest_count", "duration_minutes", "venue_type"}
                or any(not isinstance(value, str) or value not in {"operator", "structured", "intake", "defaulted", "unknown"} for value in provenance.values())):
            raise ValueError("Invalid feature provenance.")
        event_id = identifier(body["event_id"]) if body.get("event_id") is not None else None
        with self.factory.begin() as session:
            membership = active_membership(session, org, actor)
            require_permission(membership.role, Permission.EVENTS_PLAN)
            event = None
            if event_id:
                event = session.scalar(select(EventModel).where(EventModel.organization_id == org, EventModel.id == event_id).with_for_update())
                if event is None:
                    raise ResourceNotFound("Event was not found.")
            op = receipt(session, org, actor, "learning.session", body.get("idempotency_key"), body)
            if op.status == "completed":
                return deepcopy(op.response)
            if event and (event.status != "planning" or type(body.get("event_version")) is not int or event.version != body["event_version"]):
                raise StateConflict("The event changed. Reload its plan before requesting suggestions.")
            candidate_store = EventLearningStore(lambda: nullcontext(session))
            result = EventSimilarityService(candidate_store).suggestions(org, features, event_id)
            sources = (event.data or {}).get("feature_sources", {}) if event else provenance
            result["provenance"] = {key: sources.get(key, "unknown") for key in ("departments", "guest_count", "duration_minutes", "venue_type")}
            result["features"] = features
            if result["provenance"]["duration_minutes"] == "defaulted":
                result["warnings"].append("The duration is the planner default; check it before comparing past events.")
            if not result["suggestions"]:
                return complete(op, {**result, "session_id": None})
            row = SuggestionSessionModel(id=new_id(), organization_id=org, actor_user_id=actor,
                event_id=event_id, event_id_snapshot=event_id, event_version=event.version if event else None,
                event_title_snapshot=event.title if event else "",
                algorithm_version=ALGORITHM_VERSION, retrieval_version=RETRIEVAL_VERSION,
                evidence_count=result["evidence_count"], snapshot=result, applied={}, committed={})
            session.add(row)
            session.flush()
            self._audit(session, membership, row, "learning.session.created", request_id)
            return complete(op, public_session(row))

    def interact(self, org, actor, body, request_id):
        if set(body) - {"session_id", "action", "quantities", "idempotency_key", "event_version"}:
            raise ValueError("Unsupported outcome fields.")
        identity = identifier(body.get("session_id"))
        if body.get("action") not in {"ignored", "apply"}:
            raise ValueError("Choose Ignore or Apply.")
        with self.factory.begin() as session:
            membership = active_membership(session, org, actor)
            require_permission(membership.role, Permission.LEARNING_OUTCOME_WRITE)
            # Determine the parent without exposing another actor's session, then
            # acquire locks in the same event -> suggestion order as lifecycle writes.
            event_id = session.scalar(select(SuggestionSessionModel.event_id).where(
                SuggestionSessionModel.organization_id == org, SuggestionSessionModel.id == identity,
                SuggestionSessionModel.actor_user_id == actor))
            event = session.scalar(select(EventModel).where(EventModel.organization_id == org, EventModel.id == event_id).with_for_update()) if event_id else None
            row = session.scalar(select(SuggestionSessionModel).where(SuggestionSessionModel.organization_id == org,
                SuggestionSessionModel.id == identity, SuggestionSessionModel.actor_user_id == actor).with_for_update())
            if row is None:
                raise ResourceNotFound("Suggestion session was not found.")
            op = receipt(session, org, actor, "learning.interaction", body.get("idempotency_key"), body)
            if op.status == "completed":
                return deepcopy(op.response)
            shown = {item["capability"]: item["amount"] for item in row.snapshot["suggestions"]}
            quantities = body.get("quantities", {})
            if not isinstance(quantities, dict) or (body["action"] == "ignored" and quantities):
                raise ValueError("Invalid applied quantities.")
            if body["action"] == "apply" and (set(quantities) != set(shown) or any(type(v) is not int or not 1 <= v <= 10000 for v in quantities.values())):
                raise ValueError("Provide a valid quantity for every shown suggestion.")
            action = "ignored" if body["action"] == "ignored" else "applied" if quantities == shown else "applied_with_edits"
            if row.action:
                if row.action != action or row.applied != quantities:
                    raise StateConflict("This suggestion session already has a different response.")
                return complete(op, public_session(row))
            if row.event_id != event_id:
                raise StateConflict("The draft was saved elsewhere. Reload before responding.")
            if event and (event.status != "planning" or event.version != row.event_version or body.get("event_version") != event.version):
                raise StateConflict("The event changed. Reload before applying suggestions.")
            row.action, row.applied, row.acted_at = action, deepcopy(quantities), utc_now()
            self._audit(session, membership, row, "learning.suggestion." + action, request_id)
            return complete(op, public_session(row))

    @staticmethod
    def link_in_session(session, event, actor, identity):
        if identity is None:
            return
        identity = identifier(identity)
        row = session.scalar(select(SuggestionSessionModel).where(SuggestionSessionModel.organization_id == event.organization_id,
            SuggestionSessionModel.id == identity, SuggestionSessionModel.actor_user_id == actor).with_for_update())
        if row is None:
            raise ResourceNotFound("Suggestion session was not found.")
        if row.event_id_snapshot or row.action not in {"applied", "applied_with_edits"}:
            raise StateConflict("This suggestion session cannot be linked to this event.")
        row.event_id = row.event_id_snapshot = event.id
        row.event_version = event.version
        row.event_title_snapshot = event.title
        final = final_requirements({"proposal": {"requirements": (event.data or {}).get("capability_requirements", [])}})
        row.committed = {code: final.get(code, 0) for code in row.applied}

    @staticmethod
    def evaluate_in_session(session, event):
        if event.status != "returned":
            return
        session.flush()
        org = event.organization_id
        learning = session.scalar(select(EventLearningRecordModel).where(EventLearningRecordModel.organization_id == org, EventLearningRecordModel.event_id == event.id))
        if learning is None:
            return
        feedback = session.scalar(select(EventFeedbackModel).where(EventFeedbackModel.organization_id == org, EventFeedbackModel.event_id == event.id))
        items = [EventLearningStore._item(item) for item in session.scalars(select(EventFeedbackItemModel).where(EventFeedbackItemModel.organization_id == org, EventFeedbackItemModel.feedback_id == feedback.id))] if feedback else []
        example = EventLearningStore._record(learning, feedback, items)
        final = final_requirements(example)
        safe, _ = evidence_quantities(example)
        feedback_version = feedback.version if feedback else 0
        rows = session.scalars(select(SuggestionSessionModel).where(SuggestionSessionModel.organization_id == org,
            SuggestionSessionModel.event_id == event.id, SuggestionSessionModel.action.in_(["applied", "applied_with_edits"])).order_by(SuggestionSessionModel.id).with_for_update())
        for row in rows:
            previous = session.scalar(select(SuggestionEvaluationModel.id).where(SuggestionEvaluationModel.organization_id == org,
                SuggestionEvaluationModel.suggestion_session_id == row.id, SuggestionEvaluationModel.learning_version == learning.version,
                SuggestionEvaluationModel.feedback_version == feedback_version))
            if previous:
                continue
            comparisons = []
            for item in row.snapshot["suggestions"]:
                code, shown = item["capability"], item["amount"]
                applied = row.applied.get(code)
                amount = final.get(code, 0)
                if not learning.execution.get("returned") or applied is None or learning.source_type != "real":
                    outcome = "insufficient_evidence"
                elif feedback and (feedback.plan_fit != "about_right" or feedback.reuse_plan != "yes" or code not in safe and code in final or any(getattr(feedback, key) == "yes" for key in ("missing", "unnecessary", "failed", "additional_onsite"))):
                    outcome = "unresolved_feedback"
                else:
                    outcome = "removed" if amount == 0 else "retained" if amount == applied else "increased" if amount > applied else "decreased"
                comparisons.append({"capability": code, "suggested": shown, "applied": applied,
                                    "committed": row.committed.get(code), "final": amount, "outcome": outcome})
            session.add(SuggestionEvaluationModel(organization_id=org, suggestion_session_id=row.id,
                learning_version=learning.version, feedback_version=feedback_version, comparisons=comparisons))

    def evaluate(self, org, actor, identity):
        identity = identifier(identity)
        with self.factory.begin() as session:
            membership = active_membership(session, org, actor)
            require_permission(membership.role, Permission.LEARNING_HISTORY_READ)
            event = session.scalar(select(EventModel).where(EventModel.organization_id == org, EventModel.id == identity).with_for_update())
            if event is None:
                raise ResourceNotFound("Event was not found.")
            if event.status != "returned":
                raise StateConflict("The event has not been returned.")
            self.evaluate_in_session(session, event)
        return {"evaluated": True}

    def summary(self, org, actor):
        with self.factory() as session:
            membership = active_membership(session, org, actor)
            require_permission(membership.role, Permission.LEARNING_SUMMARY_READ)
            # A single statement provides consistent counts and bounded recent rows.
            actions = ("applied", "applied_with_edits", "ignored")
            counts = select(func.count().label("sessions"), *(func.sum(case((SuggestionSessionModel.action == action, 1), else_=0)).label(action) for action in actions)).where(SuggestionSessionModel.organization_id == org).subquery()
            recent = select(SuggestionSessionModel).where(SuggestionSessionModel.organization_id == org).order_by(SuggestionSessionModel.created_at.desc(), SuggestionSessionModel.id).limit(20).subquery()
            eligible = select(func.count()).select_from(EventLearningRecordModel).join(EventModel, EventModel.id == EventLearningRecordModel.event_id).where(
                EventLearningRecordModel.organization_id == org, EventModel.organization_id == org, EventModel.status == "returned",
                EventLearningRecordModel.source_type == "real", EventLearningRecordModel.eligible.is_(True), EventLearningRecordModel.exclusion_reason.is_(None)).scalar_subquery()
            latest = select(SuggestionEvaluationModel.id).where(SuggestionEvaluationModel.organization_id == org,
                SuggestionEvaluationModel.suggestion_session_id == recent.c.id).order_by(SuggestionEvaluationModel.learning_version.desc(), SuggestionEvaluationModel.feedback_version.desc()).limit(1).correlate(recent).scalar_subquery()
            versions = select(SuggestionEvaluationModel.comparisons, func.row_number().over(
                partition_by=SuggestionEvaluationModel.suggestion_session_id,
                order_by=(SuggestionEvaluationModel.learning_version.desc(), SuggestionEvaluationModel.feedback_version.desc())).label("position")).where(SuggestionEvaluationModel.organization_id == org).subquery()
            if session.bind.dialect.name == "postgresql":
                values = func.jsonb_array_elements(versions.c.comparisons).table_valued("value")
                classification = values.c.value.op("->>")("outcome")
            else:
                values = func.json_each(versions.c.comparisons).table_valued("value")
                classification = func.json_extract(values.c.value, "$.outcome")
            outcome_counts = select(classification.label("classification"), func.count().label("outcome_count")).select_from(versions).join(values, true()).where(versions.c.position == 1).group_by(classification).subquery()
            rows = session.execute(select(counts, eligible.label("eligible"), recent.c.id,
                recent.c.action.label("recent_action"), recent.c.event_id_snapshot, recent.c.event_title_snapshot, recent.c.applied,
                SuggestionEvaluationModel.comparisons, outcome_counts).select_from(counts).outerjoin(recent, true())
                .outerjoin(SuggestionEvaluationModel, SuggestionEvaluationModel.id == latest)
                .outerjoin(outcome_counts, true()).order_by(recent.c.created_at.desc(), recent.c.id)).mappings().all()
            totals = {action: rows[0][action] or 0 for action in actions}
            history, outcomes = {}, {}
            for row in rows:
                if row["classification"]:
                    outcomes[row["classification"]] = row["outcome_count"]
                if row["id"]:
                    history[row["id"]] = {"session_id": row["id"], "event_id": row["event_id_snapshot"], "title": row["event_title_snapshot"], "action": row["recent_action"], "comparisons": row["comparisons"] or []}
            return {"eligible_events": rows[0]["eligible"], "sessions": rows[0]["sessions"], "counts": totals, "outcomes": outcomes, "recent": list(history.values())}

    @staticmethod
    def _audit(session, membership, row, action, request_id):
        session.add(AuditEventModel(organization_id=row.organization_id, actor_membership_id=membership.id,
            action=action, resource_type="suggestion_session", resource_id=row.id,
            request_id=request_id, changes={"algorithm_version": row.algorithm_version}))
