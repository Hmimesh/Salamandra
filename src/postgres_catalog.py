from __future__ import annotations

import hashlib
import json
import re

from sqlalchemy import select, func

from catalog_terms import CANONICAL_TYPES, LEGACY_CODES, normalize_match
from database import (
    AuditEventModel, CatalogTermModel, OperationRequestModel, OrganizationModel,
    UserModel, active_membership, new_id, utc_now,
)
from security import Permission, ResourceNotFound, StateConflict, require_permission


class PostgresCatalog:
    def __init__(self, factory):
        self.factory = factory

    @staticmethod
    def _authorize(session, organization_id, actor_id, permission):
        member = active_membership(session, organization_id, actor_id)
        if session.scalar(select(UserModel.id).where(
            UserModel.id == actor_id, UserModel.status == "active"
        )) is None:
            raise ResourceNotFound("Active workspace account was not found.")
        require_permission(member.role, permission)
        return member

    @staticmethod
    def _view(row):
        return {"id": row.id, "kind": row.kind, "label": row.label,
                "normalized_label": row.normalized_label, "canonical_code": row.canonical_code,
                "language": row.language, "source": "workspace", "confirmed_by_user": True}

    def list_terms(self, organization_id, actor_id):
        with self.factory() as session:
            self._authorize(session, organization_id, actor_id, Permission.INVENTORY_READ)
            return [self._view(row) for row in session.scalars(select(CatalogTermModel).where(
                CatalogTermModel.organization_id == organization_id
            ).order_by(CatalogTermModel.normalized_label))]

    def create(self, organization_id, actor_id, kind, body, request_id):
        if kind not in {"alias", "category"}:
            raise ValueError("Invalid catalog term kind.")
        label, language, key = body.get("label"), body.get("language", ""), body.get("idempotency_key")
        if not isinstance(label, str) or not label.strip() or len(label) > 200 or "\x00" in label:
            raise ValueError("A category or alias label of at most 200 characters is required.")
        normalized = normalize_match(label)
        if not normalized or len(normalized) > 256:
            raise ValueError("Category or alias label cannot be matched safely.")
        if not isinstance(language, str) or len(language) > 32 or "\x00" in language:
            raise ValueError("Language label is invalid.")
        if not isinstance(key, str) or not key.strip() or len(key) > 160:
            raise ValueError("An idempotency key of at most 160 characters is required.")
        code = body.get("canonical_type", "") if kind == "alias" else ""
        if not isinstance(code, str):
            raise ValueError("Canonical category code is invalid.")
        fingerprint = hashlib.sha256(json.dumps(
            [kind, label, language, code], ensure_ascii=False
        ).encode("utf-8")).hexdigest()
        operation = f"catalog.{kind}.create"
        with self.factory.begin() as session:
            member = self._authorize(session, organization_id, actor_id, Permission.CATALOG_MANAGE)
            session.scalar(select(OrganizationModel).where(
                OrganizationModel.id == organization_id
            ).with_for_update())
            prior = session.scalar(select(OperationRequestModel).where(
                OperationRequestModel.organization_id == organization_id,
                OperationRequestModel.operation == operation,
                OperationRequestModel.idempotency_key == key,
            ))
            if prior:
                if prior.request_fingerprint != fingerprint or prior.status != "completed":
                    raise StateConflict("This idempotency key belongs to a different catalog request.")
                return prior.response
            existing = session.scalar(select(CatalogTermModel).where(
                CatalogTermModel.organization_id == organization_id,
                CatalogTermModel.normalized_label == normalized,
            ))
            if existing:
                raise StateConflict("This workspace already has a mapping for that label.")
            reserved = {normalize_match(value) for value in (*CANONICAL_TYPES, *LEGACY_CODES)}
            if normalized in reserved or re.fullmatch(r"custom [a-f0-9]{32}", normalized):
                raise StateConflict("Canonical category codes cannot be replaced by aliases.")
            count = session.scalar(select(func.count()).select_from(CatalogTermModel).where(
                CatalogTermModel.organization_id == organization_id
            ))
            if count >= 2000:
                raise StateConflict("Workspace category and alias limit reached.")
            row_id = new_id()
            if kind == "category":
                code = f"custom_{row_id}"
            elif code not in CANONICAL_TYPES:
                category = session.scalar(select(CatalogTermModel.id).where(
                    CatalogTermModel.organization_id == organization_id,
                    CatalogTermModel.kind == "category", CatalogTermModel.canonical_code == code,
                ))
                if category is None:
                    raise ValueError("Choose an existing workspace or built-in canonical category.")
            row = CatalogTermModel(
                id=row_id, organization_id=organization_id, kind=kind, label=label,
                normalized_label=normalized, canonical_code=code, language=language,
                confirmed_by=member.id,
            )
            session.add(row)
            result = self._view(row)
            session.add(OperationRequestModel(
                organization_id=organization_id, operation=operation, idempotency_key=key,
                request_fingerprint=fingerprint, status="completed", resource_id=row_id,
                response=result, completed_at=utc_now(),
            ))
            session.add(AuditEventModel(
                organization_id=organization_id, actor_membership_id=member.id,
                action=operation, resource_type="catalog_term", resource_id=row_id,
                request_id=request_id, changes=result,
            ))
            return result
