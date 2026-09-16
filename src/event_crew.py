"""Operator-maintained crew and restricted, read-only assignment credentials."""
from copy import deepcopy
from datetime import timedelta, timezone
import hashlib
import secrets
import unicodedata

from sqlalchemy import delete, func, select, update
from database import (AuditEventModel, CrewProfileModel, CrewSkillModel, CrewRoleModel,
    CrewAssignmentModel, CrewAccessModel, EventModel, FieldAdjustmentModel, active_membership, new_id, utc_now)
from equipment_conditions import inventory_label
from event_logistics import instant, ZONE
from event_returns import EventReturns
from security import Permission, ResourceNotFound, StateConflict, require_permission
from suggestion_outcomes import receipt, complete, identifier


def text(value, maximum, required=False):
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value or (required and not value.strip()):
        raise ValueError("Provide a valid crew text value.")
    return value


def level(value):
    if type(value) is not int or not 1 <= value <= 3:
        raise ValueError("Complexity and skill levels must be 1, 2 or 3.")
    return value


def skills(value):
    if not isinstance(value, dict) or len(value) > 50:
        raise ValueError("Provide at most 50 skills.")
    result = {}
    for name, proficiency in value.items():
        key = unicodedata.normalize("NFKC", text(name, 100, True)).strip().casefold()
        if key in result:
            raise ValueError("Duplicate skill.")
        result[key] = level(proficiency)
    return result


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def version(row, expected):
    if type(expected) is not int or row.version != expected:
        raise StateConflict("This record changed. Reload before editing.")


def scoped(session, model, org, identity, lock=False):
    query = select(model).where(model.organization_id == org, model.id == identifier(identity))
    row = session.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)
    if row is None:
        raise ResourceNotFound()
    return row


def audit(session, member, action, row, request_id, changes):
    session.add(AuditEventModel(organization_id=member.organization_id, actor_membership_id=member.id,
        action=action, resource_type="crew", resource_id=row.id, request_id=request_id, changes=changes))


def open_event(session, org, body):
    event = EventReturns._event(session, org, identifier(body.get("event_id")), lock=True)
    version(event, body.get("event_version"))
    if event.status in {"returned", "cancelled"}:
        raise StateConflict("This event is closed.")
    return event


def public_role(row):
    return {key: deepcopy(getattr(row, key)) for key in ("id", "name", "quantity", "complexity", "skills", "version")}


def public_assignment(row):
    return {**{key: deepcopy(getattr(row, key)) for key in ("id", "profile_id", "role_id", "status", "notes", "equipment", "version")},
        "call_at": aware(row.call_at).isoformat(), "release_at": aware(row.release_at).isoformat()}


class EventCrew:
    def __init__(self, factory):
        self.factory = factory

    def profiles(self, org, actor):
        with self.factory() as session:
            if session.get_bind().dialect.name == "postgresql":
                session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            require_permission(active_membership(session, org, actor).role, Permission.CREW_READ)
            rows = session.scalars(select(CrewProfileModel).where(CrewProfileModel.organization_id == org)
                .order_by(CrewProfileModel.name, CrewProfileModel.id).limit(500)).all()
            skill_rows = session.scalars(select(CrewSkillModel).where(CrewSkillModel.organization_id == org,
                CrewSkillModel.profile_id.in_([row.id for row in rows]))).all()
            return [{**{key: getattr(row, key) for key in ("id", "name", "kind", "active", "complexity", "contact", "notes", "version")},
                "skills": {skill.skill: skill.level for skill in skill_rows if skill.profile_id == row.id}} for row in rows]

    def profile(self, org, actor, body, request_id):
        if set(body) - {"id", "version", "name", "kind", "active", "complexity", "contact", "notes", "skills", "idempotency_key"}:
            raise ValueError("Unsupported crew profile fields.")
        values = {"name": text(body.get("name"), 200, True), "contact": text(body.get("contact", ""), 400),
            "notes": text(body.get("notes", ""), 2000), "complexity": level(body.get("complexity", 1)),
            "kind": body.get("kind"), "active": body.get("active", True)}
        if values["kind"] not in {"internal", "external"} or type(values["active"]) is not bool:
            raise ValueError("Choose a crew type and active state.")
        normalized = skills(body.get("skills", {}))
        with self.factory.begin() as session:
            member = active_membership(session, org, actor)
            require_permission(member.role, Permission.CREW_MANAGE)
            op = receipt(session, org, actor, "crew.profile", body.get("idempotency_key"), body)
            if op.status == "completed":
                return deepcopy(op.response)
            if body.get("id"):
                row = scoped(session, CrewProfileModel, org, body["id"], True)
                version(row, body.get("version"))
                row.version += 1
            else:
                row = CrewProfileModel(id=new_id(), organization_id=org, version=1)
                session.add(row)
            for key, value in values.items():
                setattr(row, key, value)
            session.flush()
            session.execute(delete(CrewSkillModel).where(CrewSkillModel.organization_id == org, CrewSkillModel.profile_id == row.id))
            session.add_all(CrewSkillModel(profile_id=row.id, organization_id=org, skill=key, level=value) for key, value in normalized.items())
            # Deactivation/type changes must not revive old credentials on reactivation.
            if not row.active or row.kind != "external":
                assignments = select(CrewAssignmentModel.id).where(CrewAssignmentModel.organization_id == org, CrewAssignmentModel.profile_id == row.id)
                session.execute(update(CrewAccessModel).where(CrewAccessModel.organization_id == org,
                    CrewAccessModel.assignment_id.in_(assignments)).values(revoked=True))
            audit(session, member, "crew.profile", row, request_id, {**values, "skills": normalized, "version": row.version})
            return complete(op, {"id": row.id, "version": row.version})

    def event(self, org, actor, identity):
        with self.factory() as session:
            if session.get_bind().dialect.name == "postgresql":
                session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            require_permission(active_membership(session, org, actor).role, Permission.CREW_READ)
            event = EventReturns._event(session, org, identifier(identity))
            roles = session.scalars(select(CrewRoleModel).where(CrewRoleModel.organization_id == org, CrewRoleModel.event_id == event.id).order_by(CrewRoleModel.id)).all()
            assignments = session.scalars(select(CrewAssignmentModel).where(CrewAssignmentModel.organization_id == org, CrewAssignmentModel.event_id == event.id).order_by(CrewAssignmentModel.call_at, CrewAssignmentModel.id)).all()
            return {"event_version": event.version, "roles": [public_role(row) for row in roles],
                "assignments": [public_assignment(row) for row in assignments], "equipment": [
                    {"id": holding.id, "name": inventory_label(holding), "quantity": line.quantity}
                    for line, holding in EventReturns._lines(session, org, event.id)]}

    def role(self, org, actor, body, request_id):
        if set(body) - {"event_id", "event_version", "id", "version", "name", "quantity", "complexity", "skills", "idempotency_key"}:
            raise ValueError("Unsupported crew role fields.")
        name, proficiency = text(body.get("name"), 200, True), skills(body.get("skills", {}))
        complexity = level(body.get("complexity", 1))
        quantity = body.get("quantity", 1)
        if type(quantity) is not int or not 1 <= quantity <= 100:
            raise ValueError("Crew quantity must be between 1 and 100.")
        with self.factory.begin() as session:
            member = active_membership(session, org, actor)
            require_permission(member.role, Permission.CREW_ASSIGN)
            op = receipt(session, org, actor, "crew.role", body.get("idempotency_key"), body)
            if op.status == "completed":
                return deepcopy(op.response)
            event = open_event(session, org, body)
            if body.get("id"):
                row = scoped(session, CrewRoleModel, org, body["id"], True)
                if row.event_id != event.id:
                    raise ResourceNotFound()
                version(row, body.get("version"))
                if session.scalar(select(CrewAssignmentModel.id).where(CrewAssignmentModel.organization_id == org,
                        CrewAssignmentModel.role_id == row.id, CrewAssignmentModel.status == "assigned").limit(1)):
                    raise StateConflict("Cancel active assignments before changing their role requirements.")
                row.version += 1
            else:
                if session.scalar(select(func.count()).select_from(CrewRoleModel).where(CrewRoleModel.organization_id == org, CrewRoleModel.event_id == event.id)) >= 100:
                    raise StateConflict("This event already has 100 crew roles.")
                row = CrewRoleModel(id=new_id(), organization_id=org, event_id=event.id, version=1)
                session.add(row)
            row.name, row.quantity, row.complexity, row.skills = name, quantity, complexity, proficiency
            event.version += 1
            audit(session, member, "crew.role", row, request_id, public_role(row))
            return complete(op, {**public_role(row), "event_version": event.version})

    def assign(self, org, actor, body, request_id):
        allowed = {"event_id", "event_version", "id", "version", "profile_id", "role_id", "call_at", "release_at",
            "status", "notes", "equipment", "override_reason", "idempotency_key"}
        if set(body) - allowed:
            raise ValueError("Unsupported assignment fields.")
        call, release = instant(body.get("call_at")), instant(body.get("release_at"))
        if call >= release or release - call > timedelta(days=31):
            raise ValueError("Choose a positive crew window of at most 31 days.")
        status = body.get("status", "assigned")
        if status not in {"assigned", "cancelled"}:
            raise ValueError("Unsupported assignment status.")
        notes, reason = text(body.get("notes", ""), 2000), text(body.get("override_reason", ""), 1000)
        equipment = body.get("equipment", [])
        if not isinstance(equipment, list) or len(equipment) > 100:
            raise ValueError("Select at most 100 equipment lines.")
        equipment = sorted({identifier(value) for value in equipment})
        with self.factory.begin() as session:
            member = active_membership(session, org, actor)
            require_permission(member.role, Permission.CREW_ASSIGN)
            if reason.strip():
                require_permission(member.role, Permission.CREW_OVERRIDE)
            op = receipt(session, org, actor, "crew.assign", body.get("idempotency_key"), body)
            if op.status == "completed":
                return deepcopy(op.response)
            event = open_event(session, org, body)
            # Every competing assignment locks the same profile before overlap reads.
            profile = scoped(session, CrewProfileModel, org, body.get("profile_id"), True)
            role = scoped(session, CrewRoleModel, org, body.get("role_id"))
            if role.event_id != event.id:
                raise ResourceNotFound()
            row = scoped(session, CrewAssignmentModel, org, body["id"], True) if body.get("id") else None
            if row:
                if (row.event_id, row.profile_id, row.role_id) != (event.id, profile.id, role.id):
                    raise ResourceNotFound()
                version(row, body.get("version"))
                if row.status == "cancelled":
                    raise StateConflict("Cancelled assignments cannot be reopened. Create a new assignment.")
            if not profile.active and status != "cancelled":
                raise StateConflict("This crew profile is inactive.")
            physical = {holding.id for _, holding in EventReturns._lines(session, org, event.id)}
            if status == "assigned" and set(equipment) - physical:
                raise ValueError("Select only equipment currently allocated to this event.")
            problems = []
            if status == "assigned":
                own_skills = dict(session.execute(select(CrewSkillModel.skill, CrewSkillModel.level).where(
                    CrewSkillModel.organization_id == org, CrewSkillModel.profile_id == profile.id)).all())
                if profile.complexity < role.complexity or any(own_skills.get(key, 0) < value for key, value in role.skills.items()):
                    problems.append("Crew skills or complexity do not meet this role.")
                overlap = session.scalar(select(CrewAssignmentModel.id).join(EventModel,
                    (EventModel.id == CrewAssignmentModel.event_id) & (EventModel.organization_id == org)).where(
                    CrewAssignmentModel.organization_id == org, CrewAssignmentModel.profile_id == profile.id,
                    CrewAssignmentModel.id != (row.id if row else ""), CrewAssignmentModel.status == "assigned",
                    EventModel.status.not_in(("returned", "cancelled")), CrewAssignmentModel.call_at < release,
                    CrewAssignmentModel.release_at > call).limit(1))
                if overlap:
                    problems.append("Crew call/release time overlaps another assignment.")
                count = session.scalar(select(func.count()).select_from(CrewAssignmentModel).where(
                    CrewAssignmentModel.organization_id == org, CrewAssignmentModel.role_id == role.id,
                    CrewAssignmentModel.status == "assigned", CrewAssignmentModel.id != (row.id if row else "")))
                if count >= role.quantity:
                    raise StateConflict("This role is already fully assigned.")
                if problems and not reason.strip():
                    raise StateConflict(" ".join(problems) + " An authorized override with a reason is required.")
            if row is None:
                row = CrewAssignmentModel(id=new_id(), organization_id=org, event_id=event.id, profile_id=profile.id,
                    role_id=role.id, version=1, updates=[])
                session.add(row)
            else:
                row.version += 1
            row.call_at, row.release_at, row.status, row.notes, row.equipment = call, release, status, notes, equipment
            row.updates = [*row.updates[-19:], {"at": utc_now().isoformat(), "message": "Assignment updated.",
                "call_at": call.isoformat(), "release_at": release.isoformat()}]
            if status == "cancelled":
                session.execute(update(CrewAccessModel).where(CrewAccessModel.organization_id == org,
                    CrewAccessModel.assignment_id == row.id).values(revoked=True))
            event.version += 1
            audit(session, member, "crew.assign", row, request_id, {**public_assignment(row), "warnings": problems, "override_reason": reason})
            return complete(op, {**public_assignment(row), "event_version": event.version, "warnings": problems})

    def access(self, org, actor, body, request_id):
        if set(body) - {"assignment_id", "version", "action", "idempotency_key"} or body.get("action") not in {"issue", "revoke"}:
            raise ValueError("Choose issue or revoke access.")
        with self.factory.begin() as session:
            member = active_membership(session, org, actor)
            require_permission(member.role, Permission.CREW_ACCESS)
            op = receipt(session, org, actor, "crew.access", body.get("idempotency_key"), body)
            if op.status == "completed":
                return {**deepcopy(op.response), "token": None}
            initial = scoped(session, CrewAssignmentModel, org, body.get("assignment_id"))
            event = EventReturns._event(session, org, initial.event_id, lock=True)
            profile = scoped(session, CrewProfileModel, org, initial.profile_id, True)
            row = scoped(session, CrewAssignmentModel, org, initial.id, True)
            version(row, body.get("version"))
            if body["action"] == "issue" and (not profile.active or profile.kind != "external" or row.status != "assigned" or event.status in {"returned", "cancelled"}):
                raise StateConflict("This assignment is not available for external access.")
            session.execute(update(CrewAccessModel).where(CrewAccessModel.organization_id == org,
                CrewAccessModel.assignment_id == row.id).values(revoked=True))
            token, expiry = None, aware(row.release_at) + timedelta(hours=24)
            if body["action"] == "issue":
                if expiry <= utc_now():
                    raise StateConflict("The assignment access window has ended.")
                token = secrets.token_urlsafe(32)
                session.add(CrewAccessModel(organization_id=org, assignment_id=row.id,
                    token_hash=hashlib.sha256(token.encode()).hexdigest(), expires_at=expiry))
            row.version += 1
            audit(session, member, "crew.access." + body["action"], row, request_id, {"expires_at": expiry.isoformat()})
            result = complete(op, {"assignment_id": row.id, "version": row.version, "expires_at": expiry.isoformat(), "action": body["action"]})
            # Receipts never contain bearer secrets. Lost responses require explicit regeneration.
            return {**result, "token": token}

    def external(self, token):
        if not isinstance(token, str) or len(token) != 43:
            raise ResourceNotFound()
        with self.factory() as session:
            # One committed snapshot for credentials, schedule and selected physical lines.
            if session.get_bind().dialect.name == "postgresql":
                session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            access = session.scalar(select(CrewAccessModel).where(CrewAccessModel.token_hash == hashlib.sha256(token.encode()).hexdigest(),
                CrewAccessModel.revoked.is_(False), CrewAccessModel.expires_at > utc_now()))
            if access is None:
                raise ResourceNotFound()
            row = scoped(session, CrewAssignmentModel, access.organization_id, access.assignment_id)
            profile = scoped(session, CrewProfileModel, access.organization_id, row.profile_id)
            event = EventReturns._event(session, access.organization_id, row.event_id)
            role = scoped(session, CrewRoleModel, access.organization_id, row.role_id)
            if not profile.active or profile.kind != "external" or row.status != "assigned" or event.status == "cancelled":
                raise ResourceNotFound()
            equipment = [{"name": inventory_label(holding), "quantity": line.quantity} for line, holding in
                EventReturns._lines(session, access.organization_id, event.id) if holding.id in row.equipment]
            logistics = (event.data or {}).get("logistics", {})
            # No general event history, crew contacts, organization IDs or private notes.
            updates = deepcopy(row.updates)
            updates.extend({"at": entry["timestamp"], "message": "Logistics schedule updated."}
                for entry in (event.data or {}).get("history", []) if entry.get("action") in {"logistics.updated", "logistics.staged"} and entry.get("timestamp"))
            if row.equipment:
                for change in session.scalars(select(FieldAdjustmentModel).where(FieldAdjustmentModel.organization_id == access.organization_id,
                        FieldAdjustmentModel.event_id == event.id, FieldAdjustmentModel.status == "fulfilled")
                        .order_by(FieldAdjustmentModel.created_at.desc(), FieldAdjustmentModel.id).limit(100)):
                    fulfillment = change.data.get("fulfillment", {})
                    if any(line.get("holding_id") in row.equipment for line in fulfillment.get("lines", [])):
                        updates.append({"at": fulfillment.get("at", aware(change.created_at).isoformat()), "message": "Your selected equipment quantities changed."})
            return {"event": event.title, "venue": (event.data or {}).get("location", ""), "status": event.status,
                "person": profile.name, "role": role.name, "call_at": aware(row.call_at).isoformat(),
                "release_at": aware(row.release_at).isoformat(), "timezone": ZONE, "notes": row.notes,
                "milestones": deepcopy(logistics.get("milestones", {})), "equipment": equipment,
                "updates": sorted(updates, key=lambda item: item["at"], reverse=True)[:20]}
