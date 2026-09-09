"""Explicit inventory archival commands using the authoritative adjustment ledger."""
from sqlalchemy import select

from database import (AuditEventModel, InventoryHoldingModel, OperationRequestModel,
                      TransactionalInventoryOperations, UserModel, new_id, utc_now)
from security import Permission, ResourceNotFound, StateConflict, require_permission


class InventoryRemoval(TransactionalInventoryOperations):
    def _member(self, session, org, actor, scope, clear):
        member = self._authorize(session, org, actor, scope, import_required=False)
        if session.scalar(select(UserModel.id).where(UserModel.id == actor, UserModel.status == "active")) is None:
            raise ResourceNotFound("Active workspace account was not found.")
        if clear:
            require_permission(member.role, Permission.INVENTORY_CLEAR)
        return member

    @staticmethod
    def _shared(session, org, *, lock=False):
        query = select(InventoryHoldingModel).where(
            InventoryHoldingModel.organization_id == org,
            InventoryHoldingModel.scope == "shared", InventoryHoldingModel.active.is_(True),
        ).order_by(InventoryHoldingModel.id)
        return list(session.scalars(query.with_for_update() if lock else query))

    def _summary(self, rows):
        return {"definitions": len(rows), "units": sum(row.available_quantity for row in rows),
                "preview_token": self._fingerprint([
                    [row.id, row.version, row.available_quantity, row.reserved_quantity,
                     row.packed_quantity, row.dispatched_quantity] for row in rows
                ])}

    def _safe(self, session, org, rows):
        if any(self._total(row) != row.available_quantity for row in rows):
            raise StateConflict("Inventory is reserved, packed or dispatched. Return or release it before clearing.")
        self._validate_dependency_graph(session, org, {}, removed_keys=[
            (row.scope, row.owner_user_id, row.legacy_item_id) for row in rows
        ])

    def preview(self, org, actor):
        with self.factory() as session:
            self._member(session, org, actor, "shared", True)
            rows = self._shared(session, org)
            result = self._summary(rows)
            try:
                self._safe(session, org, rows)
                result["blocked"] = ""
            except StateConflict as error:
                result["blocked"] = str(error)
            return result

    def execute(self, org, actor, body, request_id, *, clear=False):
        scope = "shared" if clear else body.get("scope", "shared")
        owner = actor if scope == "personal" else None
        self._validate_context(scope, owner, actor)
        key = body.get("idempotency_key")
        self._validate_operation_key(key)
        if "\x00" in key:
            raise ValueError("Invalid operation key.")
        item = body.get("item_id")
        if not clear:
            self._validate_item_id(item)
            if "\x00" in item:
                raise ValueError("Inventory item identity is invalid.")
        operation = "inventory.clear" if clear else "inventory.archive"
        payload = {"scope": scope, "owner_user_id": owner, "item_id": item if not clear else None,
                   "confirmation": body.get("confirmation"), "preview_token": body.get("preview_token")}
        digest = self._fingerprint(payload)
        with self.factory.begin() as session:
            # Compatible with event movement FK locks; holding locks protect every bucket.
            self._lock_organization(session, org, no_key_update=True)
            member = self._member(session, org, actor, scope, clear)
            if clear and (body.get("scope", "shared") != "shared" or body.get("confirmation") != "CLEAR INVENTORY"):
                raise ValueError("Type CLEAR INVENTORY to confirm clearing shared inventory.")
            prior = self._request(session, org, operation, key, lock=True)
            if prior:
                if prior.request_fingerprint != digest or prior.status != "completed":
                    raise StateConflict("This operation key belongs to another request.")
                return prior.response
            if clear:
                rows = self._shared(session, org, lock=True)
                if body.get("preview_token") != self._summary(rows)["preview_token"]:
                    raise StateConflict("Inventory changed. Review a fresh summary before clearing.")
            else:
                row = self._holding(session, org, scope, owner, item)
                if row is None or not row.active:
                    raise ResourceNotFound("Inventory item was not found.")
                if self._total(row):
                    raise StateConflict("Only an empty item definition can be archived. Remove available stock first.")
                rows = [row]
            if not rows:
                raise StateConflict("There are no active shared definitions to clear.")
            self._safe(session, org, rows)
            batch = new_id()
            result = {"archived": len(rows), "units_removed": sum(row.available_quantity for row in rows), "batch_id": batch}
            for row in rows:
                before = row.available_quantity
                row.available_quantity = 0
                row.active = False
                row.version += 1
                if before:
                    self._record_adjustment(session, row, member.id, operation, before, 0,
                                            "owner_clear", "Owner confirmed clearing shared inventory",
                                            "inventory_ui", request_id, key, batch)
                self._audit_definition(session, row, member.id, "inventory.definition_archived", request_id, batch)
            session.add(AuditEventModel(organization_id=org, actor_membership_id=member.id,
                                       action=operation, resource_type="inventory_batch", resource_id=batch,
                                       request_id=request_id, changes=result))
            session.add(OperationRequestModel(organization_id=org, operation=operation, idempotency_key=key,
                                              request_fingerprint=digest, status="completed", resource_id=batch,
                                              response=result, completed_at=utc_now()))
            return result
