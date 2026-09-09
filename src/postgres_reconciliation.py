from __future__ import annotations

from sqlalchemy import select, func

from catalog_terms import CANONICAL_TYPES, LEGACY_CODES, index_workspace_terms, normalize_match, resolve_category
from database import (AuditEventModel, CatalogDecisionModel, CatalogTermModel, InventoryHoldingModel,
                      OperationRequestModel, TransactionalInventoryOperations, new_id, utc_now)
from inventory_reconciliation import DuplicateIndex, fingerprint, review
from Item_node import ItemNode
from postgres_catalog import PostgresCatalog
from security import Permission, ResourceNotFound, StateConflict, require_permission


class PostgresReconciliation:
    """Preview is read-only. Commit owns ONE transaction for the complete import."""
    def __init__(self, factory):
        self.factory = factory
        self.catalog = PostgresCatalog(factory)
        self.stock = TransactionalInventoryOperations(factory)

    def _context(self, session, org, actor, *, cleanup=False):
        member = self.catalog._authorize(session, org, actor,
                                         Permission.CATALOG_MANAGE if cleanup else Permission.INVENTORY_IMPORT)
        if not cleanup:
            require_permission(member.role, Permission.INVENTORY_SHARED_WRITE)
        return member

    @staticmethod
    def _terms(session, org):
        return [PostgresCatalog._view(row) for row in session.scalars(
            select(CatalogTermModel).where(CatalogTermModel.organization_id == org))]

    @staticmethod
    def _holdings(session, org, *, lock=False):
        statement = select(InventoryHoldingModel).where(
            InventoryHoldingModel.organization_id == org, InventoryHoldingModel.scope == "shared",
            InventoryHoldingModel.active.is_(True),
        ).order_by(InventoryHoldingModel.id).limit(20_001)
        if lock:
            statement = statement.with_for_update()
        rows = list(session.scalars(statement))
        if len(rows) > 20_000:
            raise StateConflict("This workspace exceeds the 20,000-definition review limit.")
        return rows

    @staticmethod
    def _view(row):
        data = dict(row.data or {})
        return {"target": row.id, "item_id": row.legacy_item_id, "name": data.get("display_name") or row.legacy_item_id,
                "manufacturer": data.get("manufacturer", ""), "model": data.get("model", ""),
                "canonical_type": data.get("canonical_type") or LEGACY_CODES.get(data.get("type"), data.get("type")),
                "quantity": row.available_quantity, "location": data.get("attributes", {}).get("location", ""),
                "expected": fingerprint([row.id, row.legacy_item_id, data])}

    def preview(self, org, actor, body):
        if body.get("scope", "shared") != "shared":
            raise ValueError("This import applies to shared inventory only.")
        with self.factory() as session:
            member = self._context(session, org, actor)
            result = review(body, index_workspace_terms(self._terms(session, org)),
                            self._matching_inventory(session, org, self._holdings(session, org)))
            result["can_manage_catalog"] = member.role in {"owner", "admin"}
            custom = [term for term in self._terms(session, org) if term["kind"] == "category"]
            result["canonical_types"] = list(CANONICAL_TYPES) + [term["canonical_code"] for term in custom]
            result["category_labels"] = {term["canonical_code"]: term["label"] for term in custom}
            return result

    def commit(self, org, actor, body, request_id):
        key = body.get("idempotency_key")
        self.stock._validate_operation_key(key)
        if "\x00" in key or body.get("scope", "shared") != "shared":
            raise ValueError("Invalid import key or inventory scope.")
        # Fingerprint the requested decisions, never a quantity read outside the transaction.
        digest = fingerprint({k: body.get(k) for k in ("csv", "column_mapping", "category_choices", "decisions", "scope")})
        operation = "inventory.reviewed_import"
        with self.factory.begin() as session:
            # Serialize imports without blocking event movements' FK key-share locks.
            self.stock._lock_organization(session, org, no_key_update=True)
            member = self._context(session, org, actor)
            prior = self.stock._request(session, org, operation, key, lock=True)
            if prior:
                if prior.request_fingerprint != digest or prior.status != "completed":
                    raise StateConflict("This import key belongs to a different request.")
                return prior.response
            holdings = self._holdings(session, org, lock=True)
            terms = self._terms(session, org)
            plan = review(body, index_workspace_terms(terms), self._matching_inventory(session, org, holdings))
            if plan["counts"]["unresolved"]:
                raise StateConflict("Resolve required categories and row decisions before importing.")
            if not plan["counts"]["create"] and not plan["counts"]["add"]:
                raise ValueError("Choose at least one row to import.")
            codes, created_terms = {}, []
            for group in plan["categories"]:
                if group["action"] == "ignore":
                    continue
                code = group["code"]
                if group["action"] == "custom" or group["remember"]:
                    require_permission(member.role, Permission.CATALOG_MANAGE)
                if group["action"] == "custom":
                    term = self.catalog.create(org, actor, "category", {
                        "label": group["custom_label"], "idempotency_key": fingerprint([key, "category", group["label"]]),
                    }, request_id, session=session)
                    session.flush()
                    code = term["canonical_code"]
                    created_terms.append(term)
                codes[group["label"]] = code
                if group["remember"]:
                    normalized = normalize_match(group["label"])
                    existing = next((term for term in terms + created_terms if term["normalized_label"] == normalized), None)
                    if existing:
                        if existing["canonical_code"] != code:
                            raise StateConflict("A different workspace mapping now exists for this category.")
                    elif normalized not in {normalize_match(c) for c in (*CANONICAL_TYPES, *LEGACY_CODES)}:
                        term = self.catalog.create(org, actor, "alias", {
                            "label": group["label"], "canonical_type": code,
                            "idempotency_key": fingerprint([key, "alias", group["label"]]),
                        }, request_id, session=session)
                        session.flush()
                        created_terms.append(term)
            by_target = {row.id: row for row in holdings}
            created_rows, additions = {}, {}
            batch = new_id()
            for row in plan["rows"]:
                if row["action"] == "skip":
                    continue
                code = codes[row["type"] or "other"]
                if row["action"] == "add":
                    choice = body["decisions"][str(row["row"])]
                    target = choice.get("target")
                    holding = created_rows.get(target) if isinstance(target, str) and target.startswith("row:") else by_target.get(target)
                    if holding is None:
                        raise ResourceNotFound("Import target was not found. Refresh the review.")
                    if target not in created_rows and choice.get("expected") != self._view(holding)["expected"]:
                        raise StateConflict("An import target definition changed. Refresh the review.")
                    holding_code = self._view(holding)["canonical_type"]
                    if holding_code != code:
                        raise StateConflict("The selected target has a different category.")
                else:
                    item = ItemNode(id="item-" + new_id(), type=CANONICAL_TYPES.get(code, "other"),
                                    canonical_type=code, display_name=row["name"], category_label=row["type"],
                                    count=0, manufacturer=row["manufacturer"], model=row["model"],
                                    info=row["info"], condition=row["condition"] or "ready",
                                    attributes={"location": row["location"]} if row["location"] else {})
                    metadata = self.stock._normalize_definition_metadata(item.to_dict(), reject_restricted=False)
                    holding = InventoryHoldingModel(id=new_id(), organization_id=org, legacy_item_id=item.id,
                                                    scope="shared", available_quantity=0, active=True, data=metadata)
                    session.add(holding)
                    session.flush()
                    created_rows[row["target"]] = holding
                    self.stock._audit_definition(session, holding, member.id, "inventory.definition_created", request_id, batch)
                additions.setdefault(holding.id, [holding, 0])[1] += row["quantity"]
            # Same ledger helper as accepted manual adjustments; no nested commit or identity rewrite.
            for holding, amount in sorted(additions.values(), key=lambda pair: pair[0].id):
                before = self.stock._total(holding)
                holding.available_quantity += amount
                holding.version += 1
                self.stock._record_adjustment(session, holding, member.id, operation, before, before + amount,
                                              "reviewed_import", "Operator-confirmed CSV import", "csv_import",
                                              request_id, key, batch)
            result = {"imported": plan["counts"]["create"] + plan["counts"]["add"],
                      "counts": plan["counts"], "batch_id": batch, "catalog_terms_created": len(created_terms)}
            session.add(OperationRequestModel(organization_id=org, operation=operation, idempotency_key=key,
                                              request_fingerprint=digest, status="completed", resource_id=batch,
                                              response=result, completed_at=utc_now()))
            session.add(AuditEventModel(organization_id=org, actor_membership_id=member.id, action=operation,
                                       resource_type="inventory_import", resource_id=batch, request_id=request_id,
                                       changes=result))
            session.flush()
            return result

    def cleanup(self, org, actor):
        with self.factory() as session:
            self._context(session, org, actor, cleanup=True)
            items = [self._view(row) for row in self._holdings(session, org)]
            decisions = {row.pair_key: row.action for row in session.scalars(select(CatalogDecisionModel).where(CatalogDecisionModel.organization_id == org))}
            index, pairs, seen = DuplicateIndex(), [], set()
            counts = {"duplicates": 0, "unknown_categories": 0, "missing_manufacturer": 0, "missing_model": 0, "missing_location": 0}
            terms = index_workspace_terms(self._terms(session, org))
            issues = []
            for item in items:
                for other in index.matches(item):
                    pair = fingerprint(sorted([item["expected"], other["expected"]]))
                    if pair not in seen and pair not in decisions:
                        counts["duplicates"] += 1
                        if len(pairs) < 200:
                            pairs.append({"left": other, "right": item, "pair_key": pair, "evidence": other["evidence"]})
                    seen.add(pair)
                index.add(item)
                missing = []
                for field in ("manufacturer", "model", "location"):
                    if not item[field]:
                        counts["missing_" + field] += 1
                        missing.append(field)
                if not item["canonical_type"] or not resolve_category(item["canonical_type"], terms)["canonical_type"]:
                    counts["unknown_categories"] += 1
                    missing.append("category")
                if missing:
                    issues.append({**item, "missing": missing})
            return {"counts": counts, "pairs": pairs, "issues": issues,
                    "reviewed_pairs": len(decisions), "pair_limit": 200}

    def _matching_inventory(self, session, org, holdings):
        items = [self._view(row) for row in holdings]
        current = {item["target"]: item for item in items}
        # Reviewed product equivalence adds evidence, never automatic merge authority.
        for decision in session.scalars(select(CatalogDecisionModel).where(
            CatalogDecisionModel.organization_id == org, CatalogDecisionModel.action == "same",
        )):
            left, right = decision.data["left"], decision.data["right"]
            a, b = current.get(left["target"]), current.get(right["target"])
            if a and b and a["expected"] == left["expected"] and b["expected"] == right["expected"]:
                items.extend([{**a, "match_name": b["name"]}, {**b, "match_name": a["name"]}])
        return items

    def decide(self, org, actor, body, request_id):
        if not isinstance(body.get("action"), str) or body.get("action") not in {"same", "separate"}:
            raise ValueError("Choose same intended product or keep separate.")
        if any(not isinstance(body.get(key), str) for key in ("left", "right", "pair_key")):
            raise ValueError("Product review identifiers must be text.")
        with self.factory.begin() as session:
            self.stock._lock_organization(session, org)
            member = self._context(session, org, actor, cleanup=True)
            items = {row.id: self._view(row) for row in self._holdings(session, org)}
            left, right = items.get(body.get("left")), items.get(body.get("right"))
            if not left or not right or left["target"] == right["target"]:
                raise ResourceNotFound("Review items were not found.")
            pair = fingerprint(sorted([left["expected"], right["expected"]]))
            if pair != body.get("pair_key"):
                raise StateConflict("These definitions changed. Refresh the cleanup view.")
            existing = session.scalar(select(CatalogDecisionModel).where(CatalogDecisionModel.organization_id == org, CatalogDecisionModel.pair_key == pair))
            if existing:
                if existing.action != body["action"]:
                    raise StateConflict("This pair already has a different decision.")
                return {"saved": True}
            count = session.scalar(select(func.count()).select_from(CatalogDecisionModel).where(CatalogDecisionModel.organization_id == org))
            if count >= 2000:
                raise StateConflict("Workspace review decision limit reached (2,000).")
            record = CatalogDecisionModel(id=new_id(), organization_id=org, pair_key=pair, action=body["action"], data={"left": left, "right": right})
            session.add(record)
            session.add(AuditEventModel(organization_id=org, actor_membership_id=member.id,
                                       action="catalog.product_review", resource_type="catalog_decision", resource_id=record.id,
                                       request_id=request_id, changes={"pair_key": pair, "action": body["action"]}))
            return {"saved": True}
