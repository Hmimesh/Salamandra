"""Warehouse condition commands; event returns retain their separate ledger boundary."""
from copy import deepcopy

from sqlalchemy import func, or_, select

from database import (AuditEventModel, ConditionIncidentModel, ConditionMovementModel,
    InventoryHoldingModel, active_membership)
from security import Permission, ResourceNotFound, StateConflict, require_permission
from suggestion_outcomes import receipt, complete, identifier

TRANSITIONS = {
    "needs_repair": {"in_repair", "retired"},
    "in_repair": {"ready", "needs_repair", "retired"},
    "quarantine": {"ready", "needs_repair", "retired"},
    "missing": {"ready", "retired"},
    "ready": set(), "retired": set(),
}


def reason(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 1000 or "\x00" in value:
        raise ValueError("A condition reason of 1 to 1000 characters is required.")
    return value.strip()


def visible_holding(org, actor):
    return (InventoryHoldingModel.organization_id == org,
        or_(InventoryHoldingModel.scope == "shared", InventoryHoldingModel.owner_user_id == actor))


def inventory_label(holding):
    data = holding.data or {}
    return data.get("display_name") or " ".join(str(data.get(k) or "") for k in ("manufacturer", "model")).strip() or data.get("category_label") or data.get("type") or "Unnamed equipment"


def public_incident(row, holding):
    return {"id": row.id, "item_id": holding.legacy_item_id, "label": inventory_label(holding), "event_id": row.event_id,
        "quantity": row.quantity, "status": row.status, "issue": row.issue,
        "resolution": row.resolution, "version": row.version, "reported_at": row.created_at.isoformat(),
        "allowed_transitions": sorted(TRANSITIONS[row.status])}


class EquipmentConditions:
    def __init__(self, factory):
        self.factory = factory

    def summary(self, org, actor, scope="combined"):
        if scope not in {"shared", "personal", "combined"}:
            raise ValueError("Invalid inventory scope.")
        from database import AllocationModel, AllocationLineModel, EventModel
        with self.factory() as session:
            if session.get_bind().dialect.name == "postgresql":
                session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            require_permission(active_membership(session, org, actor).role, Permission.INVENTORY_READ)
            criteria = [*visible_holding(org, actor), InventoryHoldingModel.active.is_(True)]
            if scope != "combined":
                criteria.append(InventoryHoldingModel.scope == scope)
            buckets = ("available_quantity", "reserved_quantity", "packed_quantity", "dispatched_quantity")
            values = session.execute(select(*(func.coalesce(func.sum(getattr(InventoryHoldingModel, key)), 0) for key in buckets)).where(*criteria)).one()
            result = dict(zip(("ready", "reserved", "packed", "out"), map(int, values)))
            totals = dict(session.execute(select(ConditionIncidentModel.status, func.sum(ConditionIncidentModel.quantity))
                .join(InventoryHoldingModel, (InventoryHoldingModel.id == ConditionIncidentModel.holding_id) & (InventoryHoldingModel.organization_id == org))
                .where(ConditionIncidentModel.organization_id == org, *criteria, ConditionIncidentModel.status != "ready")
                .group_by(ConditionIncidentModel.status)).all())
            result.update({key: int(totals.get(key, 0)) for key in TRANSITIONS if key != "ready"})
            result["standby"] = int(session.scalar(select(func.coalesce(func.sum(AllocationLineModel.quantity), 0))
                .join(AllocationModel, (AllocationModel.id == AllocationLineModel.allocation_id) & (AllocationModel.organization_id == org))
                .join(EventModel, (EventModel.id == AllocationModel.event_id) & (EventModel.organization_id == org))
                .join(InventoryHoldingModel, (InventoryHoldingModel.id == AllocationLineModel.holding_id) & (InventoryHoldingModel.organization_id == org))
                .where(AllocationLineModel.organization_id == org, AllocationLineModel.state == "packed", EventModel.status == "packed",
                    EventModel.data["logistics"]["standby"].as_boolean().is_(True), *criteria)))
            result["packed"] -= result["standby"]
            return result

    def list(self, org, actor, status=None):
        if status is not None and status not in TRANSITIONS:
            raise ValueError("Unknown condition filter.")
        with self.factory() as session:
            member = active_membership(session, org, actor)
            require_permission(member.role, Permission.MAINTENANCE_READ)
            query = select(ConditionIncidentModel, InventoryHoldingModel).join(
                InventoryHoldingModel, (InventoryHoldingModel.id == ConditionIncidentModel.holding_id) &
                (InventoryHoldingModel.organization_id == ConditionIncidentModel.organization_id)).where(
                    ConditionIncidentModel.organization_id == org, *visible_holding(org, actor))
            if status is not None:
                query = query.where(ConditionIncidentModel.status == status)
            else:
                query = query.where(ConditionIncidentModel.status.not_in(["ready", "retired"]))
            rows = session.execute(query.order_by(ConditionIncidentModel.created_at.desc(), ConditionIncidentModel.id).limit(100)).all()
            # Aggregate quantities independently of the display filter and row limit.
            totals = dict(session.execute(select(ConditionIncidentModel.status,
                func.sum(ConditionIncidentModel.quantity)).join(InventoryHoldingModel,
                    (InventoryHoldingModel.id == ConditionIncidentModel.holding_id) &
                    (InventoryHoldingModel.organization_id == ConditionIncidentModel.organization_id)).where(
                        ConditionIncidentModel.organization_id == org, *visible_holding(org, actor),
                        ConditionIncidentModel.status != "ready").group_by(ConditionIncidentModel.status)).all())
            return {"incidents": [public_incident(row, holding) for row, holding in rows], "limit": 100,
                "quantities": {state: int(totals.get(state, 0)) for state in TRANSITIONS if state != "ready"}}

    def report(self, org, actor, body, request_id):
        if set(body) - {"scope", "item_id", "quantity", "status", "issue", "idempotency_key"}:
            raise ValueError("Unsupported condition report fields.")
        quantity = body.get("quantity")
        status = body.get("status")
        scope = body.get("scope", "shared")
        item = body.get("item_id")
        if type(quantity) is not int or not 1 <= quantity <= 10000:
            raise ValueError("Condition quantity must be an integer from 1 to 10000.")
        if not isinstance(status, str) or not isinstance(scope, str) or status not in {"needs_repair", "quarantine", "missing", "retired"} or scope not in {"shared", "personal"}:
            raise ValueError("Invalid condition or inventory scope.")
        if not isinstance(item, str) or not 1 <= len(item) <= 256 or "\x00" in item:
            raise ValueError("Inventory item reference is invalid.")
        issue = reason(body.get("issue"))
        with self.factory.begin() as session:
            member = active_membership(session, org, actor)
            require_permission(member.role, Permission.MAINTENANCE_MANAGE if status == "retired" else Permission.MAINTENANCE_REPORT)
            operation = receipt(session, org, actor, "condition.report", body.get("idempotency_key"), body)
            if operation.status == "completed":
                return deepcopy(operation.response)
            holding = session.scalar(select(InventoryHoldingModel).where(*visible_holding(org, actor),
                InventoryHoldingModel.scope == scope, InventoryHoldingModel.legacy_item_id == item,
                InventoryHoldingModel.active.is_(True)).with_for_update())
            if holding is None:
                raise ResourceNotFound("Inventory item was not found.")
            if holding.available_quantity < quantity:
                raise StateConflict("Only ready warehouse units can be reported here. Reconcile out equipment through its event return.")
            row = ConditionIncidentModel(organization_id=org, holding_id=holding.id,
                quantity=quantity, status=status, issue=issue, resolution="", version=1)
            session.add(row)
            session.flush()
            self._move(session, member, holding, row, "ready", status, issue, operation.id, request_id)
            return complete(operation, public_incident(row, holding))

    def transition(self, org, actor, body, request_id):
        if set(body) - {"incident_id", "version", "status", "reason", "idempotency_key"}:
            raise ValueError("Unsupported condition transition fields.")
        identity = identifier(body.get("incident_id"))
        status = body.get("status")
        if not isinstance(status, str) or status not in TRANSITIONS or type(body.get("version")) is not int or body["version"] < 1:
            raise ValueError("A valid condition and version are required.")
        note = reason(body.get("reason"))
        with self.factory.begin() as session:
            member = active_membership(session, org, actor)
            require_permission(member.role, Permission.MAINTENANCE_MANAGE)
            operation = receipt(session, org, actor, "condition.transition", body.get("idempotency_key"), body)
            if operation.status == "completed":
                return deepcopy(operation.response)
            holding = session.scalar(select(InventoryHoldingModel).join(ConditionIncidentModel,
                (ConditionIncidentModel.holding_id == InventoryHoldingModel.id) &
                (ConditionIncidentModel.organization_id == InventoryHoldingModel.organization_id)).where(
                    ConditionIncidentModel.id == identity, *visible_holding(org, actor)).with_for_update(of=InventoryHoldingModel))
            if holding is None:
                raise ResourceNotFound("Condition incident was not found.")
            row = session.scalar(select(ConditionIncidentModel).where(ConditionIncidentModel.id == identity,
                ConditionIncidentModel.organization_id == org).with_for_update())
            if row.version != body["version"] or status not in TRANSITIONS[row.status]:
                raise StateConflict("The condition changed or this transition is not allowed. Reload the incident.")
            previous = row.status
            row.status, row.resolution, row.version = status, note, row.version + 1
            self._move(session, member, holding, row, previous, status, note, operation.id, request_id)
            return complete(operation, public_incident(row, holding))

    @staticmethod
    def _move(session, member, holding, row, before_state, after_state, note, key, request_id):
        before = holding.available_quantity
        if before_state == "ready":
            holding.available_quantity -= row.quantity
            holding.condition_quantity += row.quantity
        elif after_state == "ready":
            if not holding.active or holding.condition_quantity < row.quantity:
                raise StateConflict("The holding cannot restore these units.")
            holding.available_quantity += row.quantity
            holding.condition_quantity -= row.quantity
        holding.version += 1
        session.add(ConditionMovementModel(organization_id=row.organization_id, incident_id=row.id,
            actor_membership_id=member.id, from_state=before_state, to_state=after_state,
            quantity=row.quantity, available_before=before, available_after=holding.available_quantity,
            reason=note, idempotency_key=key))
        session.add(AuditEventModel(organization_id=row.organization_id, actor_membership_id=member.id,
            action="inventory.condition", resource_type="condition_incident", resource_id=row.id,
            request_id=request_id, changes={"from": before_state, "to": after_state,
                "quantity": row.quantity, "holding_id": holding.id, "version": row.version}))
