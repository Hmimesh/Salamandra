"""Atomic reconciliation of exact dispatched holdings into ready/condition stock."""
from copy import deepcopy

from sqlalchemy import select

from database import (AllocationModel, AllocationLineModel, ConditionIncidentModel,
    InventoryHoldingModel, EventModel, StockMovementModel, TransactionalEventOperations,
    active_membership)
from equipment_conditions import EquipmentConditions, inventory_label, reason
from security import Permission, ResourceNotFound, StateConflict, require_permission
from suggestion_outcomes import receipt, complete, identifier


class EventReturns:
    def __init__(self, factory):
        self.factory = factory

    @staticmethod
    def _event(session, org, event_id, lock=False):
        query = select(EventModel).where(EventModel.organization_id == org, EventModel.id == event_id)
        event = session.scalar(query.with_for_update() if lock else query)
        if event is None:
            raise ResourceNotFound("Event was not found.")
        return event

    @staticmethod
    def _lines(session, org, event_id, lock=False):
        query = select(AllocationLineModel, InventoryHoldingModel).join(
            AllocationModel, (AllocationModel.id == AllocationLineModel.allocation_id) &
            (AllocationModel.organization_id == AllocationLineModel.organization_id)).join(
            InventoryHoldingModel, (InventoryHoldingModel.id == AllocationLineModel.holding_id) &
            (InventoryHoldingModel.organization_id == AllocationLineModel.organization_id)).where(
                AllocationModel.organization_id == org, AllocationModel.event_id == event_id,
                AllocationLineModel.state != "released"
            ).order_by(InventoryHoldingModel.id)
        return session.execute(query.with_for_update(of=InventoryHoldingModel) if lock else query).all()

    def preview(self, org, actor, event_id):
        event_id = identifier(event_id)
        with self.factory() as session:
            member = active_membership(session, org, actor)
            require_permission(member.role, Permission.OPERATIONS_RETURN)
            event = self._event(session, org, event_id)
            if event.status != "out":
                raise StateConflict("Only dispatched events can be reconciled.")
            return {"event_id": event.id, "version": event.version, "lines": [
                {"holding_id": holding.id, "quantity": line.quantity,
                 "label": inventory_label(holding)} for line, holding in self._lines(session, org, event.id)]}

    def reconcile(self, org, actor, body, request_id):
        if set(body) - {"event_id", "version", "lines", "idempotency_key"}:
            raise ValueError("Unsupported return fields.")
        event_id = identifier(body.get("event_id"))
        if type(body.get("version")) is not int or body["version"] < 1:
            raise ValueError("A current event version is required.")
        lines = body.get("lines")
        if not isinstance(lines, list) or len(lines) > 500:
            raise ValueError("Return lines must be a list of at most 500 holdings.")
        requested = {}
        for row in lines:
            if not isinstance(row, dict) or set(row) - {"holding_id", "ready", "damaged", "missing", "reason"}:
                raise ValueError("Invalid return line.")
            identity = identifier(row.get("holding_id"))
            if identity in requested or any(type(row.get(k)) is not int or row[k] < 0 for k in ("ready", "damaged", "missing")):
                raise ValueError("Return quantities must be nonnegative integers with one line per holding.")
            if row["damaged"] or row["missing"]:
                reason(row.get("reason"))
            requested[identity] = row
        with self.factory.begin() as session:
            member = active_membership(session, org, actor)
            require_permission(member.role, Permission.OPERATIONS_RETURN)
            operation = receipt(session, org, actor, "event.reconcile_return", body.get("idempotency_key"), body)
            if operation.status == "completed":
                return deepcopy(operation.response)
            event = self._event(session, org, event_id, lock=True)
            if event.status != "out" or event.version != body["version"]:
                raise StateConflict("The event changed. Reload before reconciling its return.")
            actual = self._lines(session, org, event.id, lock=True)
            if set(requested) != {holding.id for _, holding in actual}:
                raise StateConflict("Return lines must match the dispatched holdings exactly.")
            for line, holding in actual:
                row = requested[holding.id]
                if line.state != "dispatched" or sum(row[k] for k in ("ready", "damaged", "missing")) != line.quantity:
                    raise StateConflict("Account for every dispatched unit exactly once.")
            # Existing transition locks allocation/holdings in deterministic order.
            # The ready transfer and condition dispositions share this transaction;
            # no intermediate ready balance or incomplete movement is committed.
            data = deepcopy(event.data or {})
            for check in data.get("return_checklist", []):
                check["done"] = True
            event.data = data
            TransactionalEventOperations(self.factory).transition_in_session(
                session, org, event.id, "returned", member.id, request_id, operation.id)
            movement = session.scalar(select(StockMovementModel).where(
                StockMovementModel.organization_id == org, StockMovementModel.idempotency_key == operation.id))
            dispositions = []
            incidents = []
            for line, holding in actual:
                row = requested[holding.id]
                dispositions.append({"holding_id": holding.id, "quantity": line.quantity, "state": "returned",
                    **{key: row[key] for key in ("ready", "damaged", "missing")}})
                for field, status in (("damaged", "needs_repair"), ("missing", "missing")):
                    if not row[field]:
                        continue
                    incident = ConditionIncidentModel(organization_id=org, holding_id=holding.id,
                        event_id=event.id, quantity=row[field], status=status, issue=reason(row["reason"]), resolution="", version=1)
                    session.add(incident)
                    session.flush()
                    EquipmentConditions._move(session, member, holding, incident, "ready", status,
                        incident.issue, f"{operation.id}:{incident.id}", request_id)
                    incidents.append(incident.id)
            movement.lines = dispositions
            from event_learning import EventLearningStore
            EventLearningStore.sync_execution_in_session(session, event)
            return complete(operation, {"event_id": event.id, "version": event.version,
                "status": event.status, "lines": dispositions, "incident_ids": incidents})
