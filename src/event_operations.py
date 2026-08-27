from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from accounts import UserAccount
from event_memory import EventRecord
from inventory_workspace import InventoryWorkspace
from security import StateConflict


EVENT_TRANSITIONS: dict[str, frozenset[str]] = {
    "planning": frozenset({"confirmed"}),
    "confirmed": frozenset({"packed"}),
    "packed": frozenset({"out"}),
    "out": frozenset({"returned"}),
    "returned": frozenset(),
}


class EventOperations:
    def __init__(self, workspace: InventoryWorkspace):
        self.workspace = workspace

    def transition(
        self,
        event: EventRecord,
        next_status: str,
        actor: UserAccount,
    ) -> bool:
        normalized_status = str(next_status).strip().lower()
        if normalized_status not in EVENT_TRANSITIONS:
            raise ValueError("Event status is not supported.")

        if normalized_status == event.status:
            if normalized_status in {"out", "returned"}:
                return False
            raise StateConflict(f"Event is already {normalized_status}.")

        if normalized_status not in EVENT_TRANSITIONS.get(event.status, frozenset()):
            raise StateConflict(
                f"Event cannot move from {event.status} to {normalized_status}."
            )

        if normalized_status == "packed" and any(
            not item.get("done") for item in event.checklist
        ):
            raise StateConflict(
                "Complete the packing checklist before marking the event packed."
            )

        if normalized_status == "out":
            if event.conflicts:
                raise StateConflict("Resolve inventory conflicts before dispatching the event.")
            try:
                allocations = self.workspace.plan_scope_allocations(
                    event.owner_id or actor.id,
                    event.organization_id,
                    event.plan.get("lines", []),
                )
            except ValueError as error:
                raise StateConflict(str(error)) from error
            self.workspace.apply_scope_allocations(allocations, event.organization_id)
            event.movements.append(
                self._movement("dispatch", event, actor, allocations)
            )

        if normalized_status == "returned":
            if any(not item.get("done") for item in event.return_checklist):
                raise StateConflict("Complete the return checklist before closing the event.")
            dispatch = next(
                (
                    movement
                    for movement in reversed(event.movements)
                    if movement.get("action") == "dispatch"
                ),
                None,
            )
            if dispatch is None:
                raise StateConflict(
                    "This dispatched event has no authoritative movement record; reconcile it before return."
                )
            allocations = list(dispatch.get("lines", []))
            try:
                self.workspace.return_scope_allocations(allocations, event.organization_id)
            except ValueError as error:
                raise StateConflict(str(error)) from error
            event.movements.append(self._movement("return", event, actor, allocations))

        event.status = normalized_status
        event.add_history(normalized_status, actor.id, f"Event marked {normalized_status}.")
        return True

    def _movement(
        self,
        action: str,
        event: EventRecord,
        actor: UserAccount,
        lines: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "id": uuid4().hex,
            "idempotency_key": f"{event.id}:{action}",
            "organization_id": event.organization_id,
            "event_id": event.id,
            "action": action,
            "actor_id": actor.id,
            "lines": lines,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
