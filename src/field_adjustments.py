"""Requested field changes are separate from physical allocation and movements."""
from copy import deepcopy

from sqlalchemy import and_, or_, select

from database import (AllocationModel, AllocationLineModel, AuditEventModel, EventModel,
    FieldAdjustmentModel, InventoryHoldingModel, ItemClassRecordModel, StockMovementModel,
    active_membership, utc_now)
from equipment_conditions import inventory_label, reason
from event_learning import _fingerprint
from event_returns import EventReturns
from security import Permission, ResourceNotFound, StateConflict, require_permission
from suggestion_outcomes import receipt, complete, identifier


def public_adjustment(row):
    return {"id": row.id, "event_id": row.event_id, "version": row.version,
        "status": row.status, "reason": row.reason, "created_at": row.created_at.isoformat(),
        "data": deepcopy(row.data)}


class FieldAdjustments:
    def __init__(self, factory):
        self.factory = factory

    @staticmethod
    def _row(session, org, event_id, adjustment_id):
        row = session.scalar(select(FieldAdjustmentModel).where(FieldAdjustmentModel.organization_id == org,
            FieldAdjustmentModel.event_id == event_id, FieldAdjustmentModel.id == adjustment_id).with_for_update())
        if row is None:
            raise ResourceNotFound("Adjustment was not found.")
        return row

    def list(self, org, actor, event_id):
        with self.factory() as session:
            require_permission(active_membership(session, org, actor).role, Permission.STATE_READ)
            event = EventReturns._event(session, org, identifier(event_id))
            rows = session.scalars(select(FieldAdjustmentModel).where(FieldAdjustmentModel.organization_id == org,
                FieldAdjustmentModel.event_id == event.id).order_by(FieldAdjustmentModel.created_at.desc(), FieldAdjustmentModel.id).limit(100))
            physical = EventReturns._lines(session, org, event.id)
            return {"event_version": event.version, "adjustments": [public_adjustment(row) for row in rows],
                "physical": [{"holding_id": holding.id, "label": inventory_label(holding), "quantity": line.quantity,
                    "state": line.state} for line, holding in physical]}

    def create(self, org, actor, body, request_id):
        if set(body) - {"event_id", "version", "requirements", "removals", "reason", "idempotency_key"}:
            raise ValueError("Unsupported adjustment fields.")
        event_id = identifier(body.get("event_id"))
        note = reason(body.get("reason"))
        requirements, removals = body.get("requirements", []), body.get("removals", [])
        if not isinstance(requirements, list) or not isinstance(removals, list) or not 1 <= len(requirements) + len(removals) <= 100:
            raise ValueError("Supply between 1 and 100 requested changes.")
        from item_classes import CapabilityRequirement
        normalized = []
        for req in requirements:
            if not isinstance(req, dict) or set(req) - {"capability", "amount"} or not isinstance(req.get("capability"), str) or not 1 <= len(req["capability"]) <= 128 or type(req.get("amount")) is not int or not 1 <= req["amount"] <= 10000:
                raise ValueError("Each addition needs a capability and positive integer quantity.")
            normalized.append(CapabilityRequirement(req["capability"], req["amount"], source="field adjustment").to_dict())
        removed = {}
        for removal in removals:
            if not isinstance(removal, dict) or set(removal) != {"holding_id", "quantity"}:
                raise ValueError("Invalid removal.")
            key = identifier(removal.get("holding_id"))
            if key in removed or type(removal.get("quantity")) is not int or not 1 <= removal["quantity"] <= 10000:
                raise ValueError("Each removal needs a unique holding and positive quantity.")
            removed[key] = removal["quantity"]
        if normalized and removed:
            raise ValueError("Request additions and removals separately so their physical completion is explicit.")
        with self.factory.begin() as session:
            member = active_membership(session, org, actor)
            require_permission(member.role, Permission.EVENTS_ADJUST)
            op = receipt(session, org, actor, "adjustment.create", body.get("idempotency_key"), body)
            if op.status == "completed":
                return deepcopy(op.response)
            event = EventReturns._event(session, org, event_id, lock=True)
            self._editable(event, body.get("version"))
            actual = EventReturns._lines(session, org, event.id)
            quantities = {holding.id: line.quantity for line, holding in actual}
            pending_removals = {}
            for pending in session.scalars(select(FieldAdjustmentModel).where(FieldAdjustmentModel.organization_id == org,
                    FieldAdjustmentModel.event_id == event.id, FieldAdjustmentModel.status == "pending")):
                for key, amount in pending.data.get("removals", {}).items():
                    pending_removals[key] = pending_removals.get(key, 0) + amount
            if any(amount + pending_removals.get(key, 0) > quantities.get(key, 0) for key, amount in removed.items()):
                raise StateConflict("Requested removals exceed the event's unadjusted physical quantities.")
            row = FieldAdjustmentModel(organization_id=org, event_id=event.id, actor_membership_id=member.id,
                reason=note, status="pending", version=1,
                data={"before": quantities, "requirements": normalized, "removals": removed,
                    "requested": {"additions": normalized, "physical_after_removals": {key: amount - removed.get(key, 0) for key, amount in quantities.items()}}})
            session.add(row)
            session.flush()
            self._audit(session, member, event, row, "requested", request_id)
            return complete(op, {"adjustment": public_adjustment(row), "event_version": event.version})

    @staticmethod
    def _editable(event, version):
        if type(version) is not int or version != event.version or event.status not in {"packed", "out"}:
            raise StateConflict("Reload the packed or dispatched event before making this change.")

    def preview(self, org, actor, body):
        if set(body) != {"event_id", "adjustment_id"}:
            raise ValueError("Supply the event and adjustment identifiers.")
        with self.factory() as session:
            require_permission(active_membership(session, org, actor).role, Permission.EVENTS_ADJUST)
            event = EventReturns._event(session, org, identifier(body.get("event_id")))
            row = session.scalar(select(FieldAdjustmentModel).where(FieldAdjustmentModel.organization_id == org,
                FieldAdjustmentModel.event_id == event.id, FieldAdjustmentModel.id == identifier(body.get("adjustment_id"))))
            if row is None:
                raise ResourceNotFound("Adjustment was not found.")
            return self._preview(session, event, row)

    def _preview(self, session, event, row):
        if row.status != "pending" or event.status not in {"packed", "out"}:
            raise StateConflict("Only pending adjustments on active operational events can be reviewed.")
        from Inventory import Inventory
        from Item_node import ItemNode
        from item_classes import ItemClassCatalog, ConfiguredItemClass, CapabilityRequirement
        from event_planner import EventPlanner
        from presets import PresetCatalog
        holdings = session.scalars(select(InventoryHoldingModel).where(InventoryHoldingModel.organization_id == event.organization_id,
            InventoryHoldingModel.active.is_(True), or_(InventoryHoldingModel.scope == "shared",
                and_(InventoryHoldingModel.scope == "personal", InventoryHoldingModel.owner_user_id == event.owner_user_id)))
            .order_by(InventoryHoldingModel.id)).all()
        inventory = Inventory()
        for holding in holdings:
            item = ItemNode.from_dict({**holding.data, "id": holding.legacy_item_id, "count": holding.available_quantity, "in_use_count": 0})
            if item.id in inventory.items:
                inventory.items[item.id].count += item.count
            else:
                inventory.items[item.id] = item
        catalog = ItemClassCatalog()
        catalog.custom_classes[event.organization_id] = {r.class_id: ConfiguredItemClass.from_dict(r.data) for r in session.scalars(
            select(ItemClassRecordModel).where(ItemClassRecordModel.organization_id == event.organization_id))}
        requirements = [CapabilityRequirement.from_dict(req) for req in row.data["requirements"]]
        plan = EventPlanner(inventory, PresetCatalog(), item_classes=catalog, organization_id=event.organization_id).build_capability_plan(
            requirements, {"id": event.id, "event_size": (event.data or {}).get("event_size", "small")}).to_dict() if requirements else {"lines": [], "total_missing": 0}
        result = {"event_id": event.id, "event_version": event.version, "adjustment_id": row.id,
            "adjustment_version": row.version, "plan": plan, "removals": row.data["removals"],
            "waits_for_return": bool(row.data["removals"] and event.status == "out")}
        result["preview_token"] = _fingerprint(result)
        return result

    def act(self, org, actor, body, request_id, *, cancel=False):
        allowed = {"event_id", "adjustment_id", "version", "adjustment_version", "idempotency_key", "preview_token"}
        if set(body) - allowed:
            raise ValueError("Unsupported adjustment action fields.")
        with self.factory.begin() as session:
            member = active_membership(session, org, actor)
            require_permission(member.role, Permission.EVENTS_ADJUST)
            op = receipt(session, org, actor, "adjustment.cancel" if cancel else "adjustment.fulfill", body.get("idempotency_key"), body)
            if op.status == "completed":
                if not cancel:
                    action = op.response["adjustment"]["data"]["fulfillment"]["action"]
                    require_permission(member.role, Permission.OPERATIONS_DISPATCH if action == "supplemental_dispatch" else Permission.OPERATIONS_PACK)
                return deepcopy(op.response)
            event = EventReturns._event(session, org, identifier(body.get("event_id")), lock=True)
            self._editable(event, body.get("version"))
            row = self._row(session, org, event.id, identifier(body.get("adjustment_id")))
            if row.status != "pending" or type(body.get("adjustment_version")) is not int or row.version != body["adjustment_version"]:
                raise StateConflict("The adjustment changed. Reload it before continuing.")
            if not cancel:
                require_permission(member.role, Permission.OPERATIONS_DISPATCH if event.status == "out" else Permission.OPERATIONS_PACK)
                if event.status == "out" and row.data["removals"]:
                    raise StateConflict("Dispatched removals remain out until inspected return.")
                preview = self._preview(session, event, row)
                item_ids = {line["item_id"] for line in preview["plan"]["lines"] if line.get("item_id")}
                # Lock every eligible source for selected items, including empty ones,
                # then recalculate before comparing the operator-reviewed fingerprint.
                session.scalars(select(InventoryHoldingModel).where(InventoryHoldingModel.organization_id == org,
                    or_(InventoryHoldingModel.id.in_(row.data["removals"]), and_(InventoryHoldingModel.legacy_item_id.in_(item_ids),
                        or_(InventoryHoldingModel.scope == "shared", InventoryHoldingModel.owner_user_id == event.owner_user_id))))
                    .order_by(InventoryHoldingModel.id).with_for_update().execution_options(populate_existing=True)).all()
                preview = self._preview(session, event, row)
                if preview["preview_token"] != body.get("preview_token") or preview["plan"]["total_missing"]:
                    raise StateConflict("Stock or the plan changed. Review the current fulfillment preview.")
                self._fulfill(session, member, event, row, preview, op.id)
            row.status = "cancelled" if cancel else "fulfilled"
            row.version += 1
            self._audit(session, member, event, row, row.status, request_id)
            return complete(op, {"adjustment": public_adjustment(row), "event_version": event.version})

    @staticmethod
    def _audit(session, member, event, row, action, request_id):
        event.version += 1
        data = deepcopy(event.data or {})
        data["history"] = [*data.get("history", []), {"action": f"adjustment.{action}", "actor_id": member.user_id,
            "timestamp": utc_now().isoformat(), "note": row.reason}]
        event.data = data
        session.add(AuditEventModel(organization_id=event.organization_id, actor_membership_id=member.id,
            action=f"event.adjustment.{action}", resource_type="field_adjustment", resource_id=row.id,
            request_id=request_id, changes={"event_id": event.id, "version": row.version, "reason": row.reason, "data": deepcopy(row.data)}))
        from event_learning import EventLearningStore
        EventLearningStore.sync_execution_in_session(session, event)

    @staticmethod
    def _fulfill(session, member, event, row, preview, key):
        org = event.organization_id
        allocation = session.scalar(select(AllocationModel).where(AllocationModel.organization_id == org,
            AllocationModel.event_id == event.id).with_for_update())
        state = "packed" if event.status == "packed" else "dispatched"
        if allocation is None or allocation.status != state:
            raise StateConflict("The physical allocation changed.")
        lines = session.scalars(select(AllocationLineModel).where(AllocationLineModel.organization_id == org,
            AllocationLineModel.allocation_id == allocation.id, AllocationLineModel.state == state).with_for_update()).all()
        active = {line.holding_id: line for line in lines}
        movements = []
        for holding_id, amount in sorted(row.data["removals"].items()):
            line = active.get(holding_id)
            holding = session.scalar(select(InventoryHoldingModel).where(InventoryHoldingModel.organization_id == org, InventoryHoldingModel.id == holding_id))
            if line is None or line.quantity < amount or holding is None or holding.packed_quantity < amount:
                raise StateConflict("The packed quantity changed.")
            if line.quantity == amount:
                line.state = "released"
            else:
                line.quantity -= amount
                session.add(AllocationLineModel(organization_id=org, allocation_id=allocation.id, holding_id=holding_id, quantity=amount, state="released"))
            holding.packed_quantity -= amount
            holding.available_quantity += amount
            holding.version += 1
            movements.append({"holding_id": holding_id, "quantity": amount, "state": "released"})
        for planned in preview["plan"]["lines"]:
            remaining = int(planned.get("amount", 0)) - int(planned.get("missing", 0))
            if remaining <= 0:
                continue
            holdings = session.scalars(select(InventoryHoldingModel).where(InventoryHoldingModel.organization_id == org,
                InventoryHoldingModel.legacy_item_id == planned["item_id"], InventoryHoldingModel.active.is_(True),
                or_(InventoryHoldingModel.scope == "shared", InventoryHoldingModel.owner_user_id == event.owner_user_id))
                .order_by(InventoryHoldingModel.id)).all()
            for holding in holdings:
                amount = min(remaining, holding.available_quantity)
                if amount <= 0:
                    continue
                holding.available_quantity -= amount
                setattr(holding, f"{state}_quantity", getattr(holding, f"{state}_quantity") + amount)
                holding.version += 1
                if holding.id in active:
                    active[holding.id].quantity += amount
                else:
                    line = AllocationLineModel(organization_id=org, allocation_id=allocation.id, holding_id=holding.id, quantity=amount, state=state)
                    session.add(line)
                    active[holding.id] = line
                movements.append({"holding_id": holding.id, "quantity": amount, "state": state})
                remaining -= amount
                if not remaining:
                    break
            if remaining:
                raise StateConflict("The selected stock is no longer ready.")
        allocation.version += 1
        action = "release" if row.data["removals"] else "supplemental_dispatch" if state == "dispatched" else "supplemental_pack"
        session.add(StockMovementModel(organization_id=org, event_id=event.id, actor_membership_id=member.id,
            action=action, idempotency_key=key, lines=movements))
        row.data = {**row.data, "fulfillment": {"action": action, "lines": movements, "at": utc_now().isoformat()}}
        session.flush()
        totals = {}
        for line, holding in EventReturns._lines(session, org, event.id):
            totals[holding.legacy_item_id] = totals.get(holding.legacy_item_id, 0) + line.quantity
        data = deepcopy(event.data or {})
        for phase, field in (("pack", "checklist"), ("return", "return_checklist")):
            data[field] = [{"id": f"{phase}-{index}-{item}", "item_id": item, "capability": "", "amount": amount,
                "phase": phase, "done": phase == "pack"} for index, (item, amount) in enumerate(sorted(totals.items()))]
        event.data = data


def close_adjustments(session, event, member_id, request_id):
    """Called inside the authoritative terminal event transition transaction."""
    for row in session.scalars(select(FieldAdjustmentModel).where(FieldAdjustmentModel.organization_id == event.organization_id,
            FieldAdjustmentModel.event_id == event.id, FieldAdjustmentModel.status == "pending").order_by(FieldAdjustmentModel.id).with_for_update()):
        row.status = "fulfilled" if event.status == "returned" and row.data.get("removals") else "cancelled"
        row.version += 1
        row.data = {**row.data, "closure_reason": f"Event {event.status}", "closed_at": utc_now().isoformat()}
        session.add(AuditEventModel(organization_id=event.organization_id, actor_membership_id=member_id,
            action=f"event.adjustment.{row.status}", resource_type="field_adjustment", resource_id=row.id,
            request_id=request_id, changes={"event_id": event.id, "closure_reason": row.data["closure_reason"]}))
