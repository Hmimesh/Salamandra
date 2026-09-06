from __future__ import annotations

import csv
import email.utils
import hashlib
import io
import ipaddress
import json
import logging
import mimetypes
import os
import secrets
import threading
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from accounts import AccountStore, UserAccount
from event_intake import EventDescriptionPlanner
from event_memory import EventMemory, EventRecord
from event_operations import EventOperations
from event_planner import EventPlanner
from event_templates import TemplateCatalog
from inventory_workspace import PERSONAL_SCOPE, SHARED_SCOPE, InventoryWorkspace
from inventory_dependencies import (
    DependencyNode,
    normalize_dependency_requirements,
    validate_dependency_updates,
)
from integrations import IntegrationStore
from Item_node import ItemNode, Requirement
from item_classes import ConfiguredItemClass, DependencyRule, ItemClassCatalog
from kits import KitStore
from presets import PresetCatalog
from security import (
    AccessDenied,
    ApiError,
    AuthenticationRequired,
    Permission,
    ResourceNotFound,
    StateConflict,
    require_permission,
    validate_assignable_role,
)
from operational_logging import configure_logging, log_event
from readiness import DatabaseReadiness, ReadinessResult
from web_config import ConfigurationError, WebConfig, normalize_host, normalize_origin


ROOT_DIR = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT_DIR / "web"
DOCS_DIR = ROOT_DIR / "docs"
INVENTORY_PATH = DOCS_DIR / "inventory.json"
WORKSPACE_PATH = DOCS_DIR / "inventories.json"
EVENTS_PATH = DOCS_DIR / "events.json"
USERS_PATH = DOCS_DIR / "users.json"
ITEM_CLASSES_PATH = DOCS_DIR / "item_classes.json"
INTEGRATIONS_PATH = DOCS_DIR / "integrations.json"
KITS_PATH = DOCS_DIR / "kits.json"
MAX_JSON_BODY_BYTES = 1_000_000
ADVANCED_INVENTORY_FIELDS = frozenset(
    {
        "attributes",
        "capabilities",
        "class_id",
        "connectors",
        "preference_score",
        "quality_score",
    }
)
LOGGER = logging.getLogger(__name__)
LOGGER.addHandler(logging.NullHandler())


class SalamandraServer(BaseHTTPRequestHandler):
    accounts = AccountStore(USERS_PATH)
    workspace = InventoryWorkspace(WORKSPACE_PATH, INVENTORY_PATH)
    catalog = PresetCatalog()
    templates = TemplateCatalog()
    memory = EventMemory(EVENTS_PATH)
    item_classes = ItemClassCatalog(ITEM_CLASSES_PATH)
    integrations = IntegrationStore(INTEGRATIONS_PATH)
    kits = KitStore(KITS_PATH)
    sessions: dict[str, dict[str, float | str]] = {}
    login_attempts: dict[str, list[float]] = {}
    registration_attempts: dict[str, list[float]] = {}
    event_create_requests: dict[tuple[str, str], dict[str, str]] = {}
    operation_lock = threading.RLock()
    database_runtime = None
    readiness_probe = None
    web_config = WebConfig.local_default()

    def do_GET(self):
        parsed_url = urlparse(self.path)
        try:
            self.validate_request_host()
            self.validate_request_origin(require=False)
            self._do_GET()
        except ApiError as error:
            self.log_api_error(error, parsed_url.path)
            self.send_json_error(str(error), error.status)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            self.send_json_error(str(error) or "Request is invalid.", HTTPStatus.BAD_REQUEST)
        except Exception:
            log_event(
                LOGGER,
                logging.ERROR,
                "unexpected_server_error",
                request_id=self.correlation_id(),
                action=parsed_url.path,
                result="error",
            )
            LOGGER.exception("Unhandled Salamandra GET error")
            self.send_json_error(
                "Salamandra could not complete that request.",
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _do_GET(self):
        parsed_url = urlparse(self.path)

        if parsed_url.path == "/health":
            self.send_json({"status": "ok"})
            return

        if parsed_url.path == "/ready":
            result = (
                self.readiness_probe.check()
                if self.readiness_probe is not None
                else ReadinessResult(False, "database_unavailable")
            )
            if not result.ready:
                log_event(
                    LOGGER,
                    logging.WARNING,
                    "readiness_failed",
                    request_id=self.correlation_id(),
                    action="readiness",
                    result=result.code,
                )
                self.send_json(
                    {"status": "not_ready"},
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
                return
            self.send_json({"status": "ready"})
            return

        user = self.current_user()

        if parsed_url.path == "/api/state":
            if user is not None:
                require_permission(user.role, Permission.STATE_READ)
            self.send_json(self.state_payload(user))
            return

        if parsed_url.path == "/api/inventory/export.csv":
            user = self.require_user()
            require_permission(user.role, Permission.INVENTORY_EXPORT)
            self.export_inventory_csv(user)
            return

        if parsed_url.path == "/api/presets":
            user = self.require_user()
            require_permission(user.role, Permission.INVENTORY_READ)
            query = parse_qs(parsed_url.query)
            tag = query.get("tag", [None])[0]
            presets = [preset.to_dict() for preset in self.catalog.list_presets(tag)]
            self.send_json({"presets": presets, "tags": self.catalog.to_dict()["tags"]})
            return

        if parsed_url.path == "/api/templates":
            user = self.require_user()
            require_permission(user.role, Permission.EVENTS_PLAN)
            suggestions = self.templates.to_dict()
            self.send_json(
                {
                    "templates": [],
                    "kits": [kit.to_dict() for kit in self.kits.list_kits(user.organization_id)],
                    "suggested_templates": suggestions["templates"],
                    "suggested_kits": suggestions["kits"],
                }
            )
            return

        if parsed_url.path == "/api/sync/status":
            user = self.require_user()
            require_permission(user.role, Permission.STATE_READ)
            self.send_json({"sync": self.sync_payload(user)})
            return

        if parsed_url.path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return

        self.serve_static_file(parsed_url.path)

    def do_POST(self):
        parsed_url = urlparse(self.path)

        try:
            self.validate_request_host()
            self.validate_request_origin(require=self.web_config.production_like)
            body = self.read_json_body()

            if parsed_url.path == "/api/auth/signin":
                self.handle_sign_in(body)
                return

            if parsed_url.path == "/api/auth/register":
                self.handle_registration(body)
                return

            if parsed_url.path == "/api/auth/demo":
                self.handle_demo_sign_in()
                return

            if parsed_url.path == "/api/auth/signout":
                self.handle_sign_out()
                return

            user = self.require_user()

            if parsed_url.path == "/api/inventory/items":
                scope = self.authorized_inventory_scope(body, user)
                self.validate_inventory_metadata_access(body, user)
                item = self.create_item_from_body(body)
                self.validate_local_item_dependencies(item, scope, user)
                operation_key = self.operation_request_id(body)
                stored_item = self.workspace.add_item(
                    item,
                    amount=item.count,
                    scope=scope,
                    user_id=user.id,
                    organization_id=user.organization_id,
                    request_id=operation_key,
                    reason_code="stock_received",
                    reason=str(body.get("reason", "Inventory received.")),
                    source="inventory_ui",
                )
                self.save_workspace()
                self.send_json({"item": stored_item.to_dict(), "state": self.state_payload(user)})
                return

            if parsed_url.path == "/api/inventory/items/update":
                require_permission(user.role, Permission.INVENTORY_DEFINITION_MANAGE)
                scope = self.authorized_inventory_scope(body, user)
                operation_key = self.operation_request_id(body)
                updated_item = self.create_item_from_body(body)
                self.validate_local_item_dependencies(
                    updated_item,
                    scope,
                    user,
                    replaced_item_id=str(body.get("original_id", body.get("id", ""))),
                )
                updated_item = self.workspace.update_item(
                    body.get("original_id", body.get("id", "")),
                    updated_item,
                    scope=scope,
                    user_id=user.id,
                    organization_id=user.organization_id,
                    request_id=operation_key,
                )
                self.save_workspace()
                self.send_json(
                    {"item": updated_item.to_dict(), "state": self.state_payload(user)}
                )
                return

            if parsed_url.path == "/api/item-classes":
                require_permission(user.role, Permission.ITEM_CLASSES_MANAGE)
                stored_class = self.item_classes.upsert(
                    self.create_item_class_from_body(body, user.organization_id),
                    user.organization_id,
                )
                self.send_json(
                    {
                        "item_class": stored_class.to_dict(),
                        "state": self.state_payload(user),
                    },
                    status=HTTPStatus.CREATED,
                )
                return

            if parsed_url.path == "/api/item-classes/clone":
                require_permission(user.role, Permission.ITEM_CLASSES_MANAGE)
                stored_class = self.item_classes.clone(
                    body.get("source_id", ""),
                    body.get("id", ""),
                    body.get("name", ""),
                    user.organization_id,
                )
                self.send_json(
                    {
                        "item_class": stored_class.to_dict(),
                        "state": self.state_payload(user),
                    },
                    status=HTTPStatus.CREATED,
                )
                return

            if parsed_url.path == "/api/item-classes/remove":
                require_permission(user.role, Permission.ITEM_CLASSES_MANAGE)
                if not self.item_classes.remove(
                    body.get("class_id", ""),
                    user.organization_id,
                ):
                    self.send_json_error(
                        "Only a custom class in this workspace can be removed.",
                        HTTPStatus.NOT_FOUND,
                    )
                    return
                self.send_json({"state": self.state_payload(user)})
                return

            if parsed_url.path == "/api/inventory/presets":
                self.validate_inventory_metadata_access(body, user)
                scope = self.authorized_inventory_scope(body, user)
                preset = self.catalog.get(body.get("preset_id", ""))
                if preset is None:
                    self.send_json_error("Preset was not found.", HTTPStatus.NOT_FOUND)
                    return

                stored_item = self.workspace.add_from_preset(
                    preset,
                    scope=scope,
                    user_id=user.id,
                    amount=self.optional_int(body.get("amount")),
                    organization_id=user.organization_id,
                    request_id=self.operation_request_id(body),
                )
                self.save_workspace()
                self.send_json({"item": stored_item.to_dict(), "state": self.state_payload(user)})
                return

            if parsed_url.path == "/api/inventory/use":
                scope = self.authorized_inventory_scope(body, user)
                if self.database_runtime is not None:
                    raise StateConflict(
                        "Direct inventory checkout is disabled. Dispatch an event or kit instead."
                    )
                if not self.workspace.use_item(
                    body.get("item_id", ""),
                    self.required_amount(body),
                    scope,
                    user.id,
                    user.organization_id,
                ):
                    self.send_json_error("Item was not found.", HTTPStatus.NOT_FOUND)
                    return
                self.save_workspace()
                self.send_json({"state": self.state_payload(user)})
                return

            if parsed_url.path == "/api/inventory/return":
                scope = self.authorized_inventory_scope(body, user)
                if self.database_runtime is not None:
                    raise StateConflict(
                        "Direct inventory return is disabled. Return the source event instead."
                    )
                if not self.workspace.return_item(
                    body.get("item_id", ""),
                    self.required_amount(body),
                    scope,
                    user.id,
                    user.organization_id,
                ):
                    self.send_json_error("Item was not found.", HTTPStatus.NOT_FOUND)
                    return
                self.save_workspace()
                self.send_json({"state": self.state_payload(user)})
                return

            if parsed_url.path == "/api/inventory/remove":
                scope = self.authorized_inventory_scope(body, user)
                if not self.workspace.remove_item(
                    body.get("item_id", ""),
                    self.required_amount(body),
                    scope,
                    user.id,
                    user.organization_id,
                    request_id=self.operation_request_id(body),
                    reason_code=str(body.get("reason_code", "stock_removed")),
                    reason=str(body.get("reason", "Inventory removed.")),
                    source="inventory_ui",
                ):
                    self.send_json_error("Item was not found.", HTTPStatus.NOT_FOUND)
                    return
                self.save_workspace()
                self.send_json({"state": self.state_payload(user)})
                return

            if parsed_url.path == "/api/inventory/save":
                require_permission(user.role, Permission.INVENTORY_SHARED_WRITE)
                self.save_workspace()
                self.send_json({"state": self.state_payload(user)})
                return

            if parsed_url.path == "/api/events/plan":
                require_permission(user.role, Permission.EVENTS_PLAN)
                plan = self.build_event_plan(body, user)
                self.send_json({"plan": plan.to_dict(), "state": self.state_payload(user)})
                return

            if parsed_url.path == "/api/events/template":
                require_permission(user.role, Permission.EVENTS_PLAN)
                plan_request = self.plan_request_from_template(body, user)
                plan = self.build_event_plan(plan_request, user)
                self.send_json(
                    {
                        "request": plan_request,
                        "plan": plan.to_dict(),
                        "state": self.state_payload(user),
                    }
                )
                return

            if parsed_url.path == "/api/kits/checkout":
                require_permission(user.role, Permission.OPERATIONS_DISPATCH)
                event, created = self.checkout_kit(body, user)
                self.send_json(
                    {
                        "event": event.to_dict(),
                        "plan": event.plan,
                        "state": self.state_payload(user),
                    },
                    HTTPStatus.CREATED if created else HTTPStatus.OK,
                )
                return

            if parsed_url.path == "/api/kits/create":
                require_permission(user.role, Permission.KITS_MANAGE)
                operation_key = self.operation_request_id(body)
                if self.database_runtime is not None:
                    kit = self.kits.create(
                        body,
                        user.organization_id,
                        user.id,
                        self.correlation_id(),
                        operation_key,
                    )
                else:
                    with self.operation_lock:
                        kit = self.kits.create(body, user.organization_id)
                self.send_json(
                    {"kit": kit.to_dict(), "state": self.state_payload(user)},
                    HTTPStatus.CREATED,
                )
                return

            if parsed_url.path == "/api/events/return":
                require_permission(user.role, Permission.OPERATIONS_RETURN)
                if self.database_runtime is not None:
                    event_id = self.required_identifier(body.get("event_id"), "event_id")
                    event = self.database_runtime.transition(
                        event_id,
                        "returned",
                        user,
                        self.request_id(),
                    )
                else:
                    with self.operation_lock:
                        event = self.event_from_body(body, user)
                        changed = EventOperations(self.workspace).transition(
                            event, "returned", user
                        )
                        if changed:
                            self.save_workspace()
                        self.memory.add(event)
                self.send_json({"event": event.to_dict(), "state": self.state_payload(user)})
                return

            if parsed_url.path == "/api/events/checklist":
                phase = body.get("phase", "pack")
                if phase not in {"pack", "return"}:
                    raise ValueError("Checklist phase is not supported.")
                if phase == "return":
                    require_permission(user.role, Permission.OPERATIONS_RETURN)
                else:
                    require_permission(user.role, Permission.OPERATIONS_PACK)
                item_id = self.required_identifier(body.get("item_id"), "item_id")
                if self.database_runtime is not None:
                    event = self.database_runtime.update_checklist(
                        self.required_identifier(body.get("event_id"), "event_id"),
                        phase,
                        item_id,
                        bool(body.get("done", True)),
                        user,
                        self.request_id(),
                    )
                else:
                    event = self.event_from_body(body, user)
                    if phase == "return" and event.status != "out":
                        raise StateConflict("Return checklist is available only after dispatch.")
                    if phase == "pack" and event.status != "confirmed":
                        raise StateConflict("Packing checklist is available only for confirmed events.")
                    checklist = event.return_checklist if phase == "return" else event.checklist
                    matched = False
                    for item in checklist:
                        if item.get("item_id") == item_id:
                            item["done"] = bool(body.get("done", True))
                            matched = True
                    if not matched:
                        raise ResourceNotFound("Checklist item was not found.")
                    event.add_history("checklist", user.id, f"{phase} checklist updated.")
                    self.memory.add(event)
                self.send_json({"event": event.to_dict(), "state": self.state_payload(user)})
                return

            if parsed_url.path == "/api/events/status":
                next_status = str(body.get("status", "")).strip().lower()
                transition_permissions = {
                    "confirmed": Permission.EVENTS_UPDATE,
                    "packed": Permission.OPERATIONS_PACK,
                    "out": Permission.OPERATIONS_DISPATCH,
                    "returned": Permission.OPERATIONS_RETURN,
                }
                permission = transition_permissions.get(next_status)
                if permission is None:
                    raise ValueError("Event status is not supported.")
                require_permission(user.role, permission)
                if self.database_runtime is not None:
                    event_id = self.required_identifier(body.get("event_id"), "event_id")
                    event = self.database_runtime.transition(
                        event_id,
                        next_status,
                        user,
                        self.request_id(),
                    )
                else:
                    with self.operation_lock:
                        event = self.event_from_body(body, user)
                        changed = EventOperations(self.workspace).transition(
                            event, next_status, user
                        )
                        if changed:
                            if next_status in {"out", "returned"}:
                                self.save_workspace()
                            self.memory.add(event)
                self.send_json({"event": event.to_dict(), "state": self.state_payload(user)})
                return

            if parsed_url.path == "/api/events/cancel":
                require_permission(user.role, Permission.EVENTS_CANCEL)
                event_id = self.required_identifier(body.get("event_id"), "event_id")
                if self.database_runtime is not None:
                    event = self.database_runtime.cancel_event(
                        event_id, user, self.correlation_id()
                    )
                else:
                    with self.operation_lock:
                        event = self.event_from_body(body, user)
                        if event.status == "cancelled":
                            pass
                        elif event.status not in {"planning", "confirmed", "packed"}:
                            raise StateConflict(
                                "Dispatched and returned events cannot be cancelled."
                            )
                        else:
                            event.status = "cancelled"
                            event.version += 1
                            event.add_history(
                                "cancelled",
                                user.id,
                                "Event cancelled. Reserved inventory was released.",
                            )
                            self.memory.add(event)
                self.send_json({"event": event.to_dict(), "state": self.state_payload(user)})
                return

            if parsed_url.path == "/api/events/delete":
                require_permission(user.role, Permission.EVENTS_DELETE)
                if body.get("confirm") is not True:
                    raise ValueError("Confirm permanent deletion before continuing.")
                event_id = self.required_identifier(body.get("event_id"), "event_id")
                if self.database_runtime is not None:
                    self.database_runtime.delete_event(
                        event_id, user, self.correlation_id()
                    )
                else:
                    with self.operation_lock:
                        event = self.event_from_body(body, user)
                        if event.status != "planning" or event.movements:
                            raise StateConflict(
                                "Only an unconfirmed planning event without operational history can be deleted."
                            )
                        self.memory.remove(event_id, user.organization_id)
                self.send_json({"state": self.state_payload(user)})
                return

            if parsed_url.path == "/api/events/describe":
                require_permission(user.role, Permission.EVENTS_PLAN)
                draft = EventDescriptionPlanner(
                    self.workspace.combined_inventory(user.id, user.organization_id),
                    self.catalog,
                    self.memory,
                    organization_id=user.organization_id,
                    item_classes=self.item_classes,
                ).draft_from_description(
                    body.get("description", ""),
                    overrides=body.get("overrides"),
                )
                draft.record.owner_id = user.id
                draft.record.assigned_user_ids = self.valid_event_assignees(
                    draft.record.assigned_user_ids, user
                )
                self.send_json({"draft": draft.to_dict(), "state": self.state_payload(user)})
                return

            if parsed_url.path == "/api/events/save":
                require_permission(user.role, Permission.EVENTS_CREATE)
                description, overrides = self.event_request_parts(body)
                request_payload = {
                    "description": description,
                    "overrides": overrides,
                }
                idempotency_key = self.operation_request_id(body)
                event = self.build_event_from_description(
                    description, overrides, user
                )
                event.prepare_operations(user.id)
                if self.database_runtime is not None:
                    event, created = self.database_runtime.create_event(
                        event,
                        user,
                        idempotency_key,
                        self.correlation_id(),
                        request_payload,
                    )
                else:
                    fingerprint = hashlib.sha256(
                        json.dumps(
                            request_payload,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest()
                    key = (user.organization_id, idempotency_key)
                    with self.operation_lock:
                        existing = self.event_create_requests.get(key)
                        if existing is not None:
                            if existing["fingerprint"] != fingerprint:
                                raise StateConflict(
                                    "This idempotency key was already used for another event."
                                )
                            event = self.memory.get_for_organization(
                                existing["event_id"], user.organization_id
                            )
                            if event is None:
                                raise StateConflict(
                                    "The completed event is unavailable."
                                )
                            created = False
                        else:
                            self.apply_allocation_updates(event, user)
                            self.memory.add(event)
                            self.event_create_requests[key] = {
                                "fingerprint": fingerprint,
                                "event_id": event.id,
                            }
                            created = True
                if (
                    created
                    and self.database_runtime is not None
                    and event.plan.get("allocation_updates")
                ):
                    self.apply_allocation_updates(event, user)
                    event = self.database_runtime.update_planning_plan(
                        event,
                        user,
                        self.correlation_id(),
                        "Overlap allocations updated when the event was created.",
                    )
                self.send_json(
                    {"event": event.to_dict(), "state": self.state_payload(user)},
                    status=HTTPStatus.CREATED if created else HTTPStatus.OK,
                )
                return

            if parsed_url.path == "/api/events/update":
                require_permission(user.role, Permission.EVENTS_UPDATE)
                event_id = self.required_identifier(body.get("event_id"), "event_id")
                current = self.memory.get_for_organization(
                    event_id, user.organization_id
                )
                if current is None:
                    raise ResourceNotFound("Event was not found.")
                try:
                    expected_version = int(body.get("version"))
                except (TypeError, ValueError) as error:
                    raise ValueError("Event version is invalid.") from error
                description, overrides = self.event_request_parts(
                    body,
                    reject_ids=False,
                    fallback_event=current,
                )
                if self.database_runtime is not None:
                    updated = self.build_event_from_description(
                        description,
                        overrides,
                        user,
                        exclude_event_id=event_id,
                        inventory_user_id=current.owner_id,
                        optimize_overlaps=False,
                        owner_user_id=current.owner_id,
                    )
                    event = self.database_runtime.update_event(
                        event_id,
                        expected_version,
                        updated,
                        user,
                        self.correlation_id(),
                    )
                else:
                    with self.operation_lock:
                        current = self.memory.get_for_organization(
                            event_id, user.organization_id
                        )
                        if current is None:
                            raise ResourceNotFound("Event was not found.")
                        if current.status != "planning":
                            raise StateConflict(
                                "Only planning events can be edited. Confirmed, packed, "
                                "dispatched, and returned events are locked to protect inventory history."
                            )
                        if current.version != expected_version:
                            raise StateConflict(
                                "This event changed after you opened it. Reload and review the latest version."
                            )
                        event = self.build_event_from_description(
                            description,
                            overrides,
                            user,
                            exclude_event_id=event_id,
                            inventory_user_id=current.owner_id,
                            optimize_overlaps=False,
                            owner_user_id=current.owner_id,
                        )
                        event.id = current.id
                        event.owner_id = current.owner_id
                        event.source_type = current.source_type
                        event.source_id = current.source_id
                        event.created_at = current.created_at
                        event.version = current.version + 1
                        event.history = list(current.history)
                        event.add_history(
                            "edited", user.id, "Event details and plan updated."
                        )
                        self.memory.add(event)
                self.send_json(
                    {"event": event.to_dict(), "state": self.state_payload(user)}
                )
                return

            if parsed_url.path == "/api/account/profile":
                updated_user = self.accounts.update_profile(
                    user.id,
                    name=body.get("name", ""),
                    title=body.get("title", ""),
                    warehouse=body.get("warehouse", ""),
                )
                self.send_json(
                    {"user": updated_user.to_public_dict(), "state": self.state_payload(updated_user)}
                )
                return

            if parsed_url.path == "/api/account/preferences":
                updated_user = self.accounts.update_preferences(
                    user.id,
                    theme=body.get("theme", user.preferences.get("theme", "system")),
                    font_scale=body.get(
                        "font_scale",
                        user.preferences.get("font_scale", "comfortable"),
                    ),
                    density=body.get(
                        "density",
                        user.preferences.get("density", "comfortable"),
                    ),
                    show_progress=body.get(
                        "show_progress",
                        user.preferences.get("show_progress", True),
                    ),
                    onboarding_dismissed=body.get(
                        "onboarding_dismissed",
                        user.preferences.get("onboarding_dismissed", False),
                    ),
                )
                self.send_json(
                    {"user": updated_user.to_public_dict(), "state": self.state_payload(updated_user)}
                )
                return

            if parsed_url.path == "/api/integrations/configure":
                require_permission(user.role, Permission.INTEGRATIONS_MANAGE)
                integration = self.integrations.configure(
                    user.organization_id,
                    body.get("integration_id", ""),
                    body,
                )
                self.send_json(
                    {"integration": integration, "state": self.state_payload(user)}
                )
                return

            if parsed_url.path == "/api/inventory/import.csv":
                require_permission(user.role, Permission.INVENTORY_IMPORT)
                imported = self.import_inventory_csv(body, user)
                self.send_json(
                    {"imported": imported, "state": self.state_payload(user)}
                )
                return

            if parsed_url.path == "/api/inventory/import.preview":
                require_permission(user.role, Permission.INVENTORY_IMPORT)
                self.send_json(self.inventory_csv_preview(body))
                return

            if parsed_url.path == "/api/team/invite":
                require_permission(user.role, Permission.MEMBERS_INVITE)
                new_user = self.accounts.create_user(
                    name=body.get("name", ""),
                    email=body.get("email", ""),
                    password=body.get("password", ""),
                    role=validate_assignable_role(
                        user.role,
                        str(body.get("role", "operator")),
                    ),
                    organization_id=user.organization_id,
                    organization_name=user.organization_name,
                    title=body.get("title", "Event Operations"),
                    warehouse=body.get("warehouse", user.warehouse),
                )
                self.send_json(
                    {"user": new_user.to_public_dict(), "state": self.state_payload(user)},
                    status=HTTPStatus.CREATED,
                )
                return

            if parsed_url.path == "/api/sync/run":
                require_permission(user.role, Permission.SYNC_RUN)
                self.send_json({"sync": self.sync_payload(user), "state": self.state_payload(user)})
                return

            self.send_json_error("Endpoint was not found.", HTTPStatus.NOT_FOUND)
        except ApiError as error:
            self.log_api_error(error, parsed_url.path)
            self.send_json_error(str(error), error.status)
        except ValueError as error:
            self.send_json_error(str(error), HTTPStatus.BAD_REQUEST)
        except (json.JSONDecodeError, TypeError, KeyError):
            self.send_json_error("Request body must be valid JSON.", HTTPStatus.BAD_REQUEST)
        except Exception:
            log_event(
                LOGGER,
                logging.ERROR,
                "unexpected_server_error",
                request_id=self.correlation_id(),
                actor_id=getattr(getattr(self, "_request_actor", None), "id", None),
                organization_id=getattr(
                    getattr(self, "_request_actor", None), "organization_id", None
                ),
                action=parsed_url.path,
                result="error",
            )
            LOGGER.exception("Unhandled Salamandra API error")
            self.send_json_error("Salamandra could not complete that request.", HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_OPTIONS(self):
        try:
            self.validate_request_host()
            self.validate_request_origin(require=True)
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_security_headers()
            self.send_cors_headers()
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header(
                "Access-Control-Allow-Headers",
                "Content-Type, Idempotency-Key, X-Request-ID",
            )
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("X-Request-ID", self.correlation_id())
            self.end_headers()
        except ApiError as error:
            self.log_api_error(error, urlparse(self.path).path)
            self.send_json_error(str(error), error.status)

    def handle_sign_in(self, body: dict):
        email = body.get("email", "")
        password = body.get("password", "")
        if not isinstance(email, str) or not isinstance(password, str):
            raise ValueError("Email and password must be strings.")
        attempt_key = f"{self.client_ip()}:{email.strip().lower()}"
        cutoff = time.time() - 300
        attempts = [
            attempt
            for attempt in self.login_attempts.get(attempt_key, [])
            if attempt >= cutoff
        ]
        if len(attempts) >= 5:
            log_event(
                LOGGER,
                logging.WARNING,
                "authentication_throttled",
                request_id=self.correlation_id(),
                action="signin",
                result="throttled",
            )
            raise ApiError("Too many sign-in attempts. Try again later.", 429)
        user = self.accounts.authenticate(email, password)
        if user is None:
            attempts.append(time.time())
            self.login_attempts[attempt_key] = attempts
            log_event(
                LOGGER,
                logging.INFO,
                "authentication_failed",
                request_id=self.correlation_id(),
                action="signin",
                result="denied",
            )
            self.send_json_error("Email or password is incorrect.", HTTPStatus.UNAUTHORIZED)
            return

        self.login_attempts.pop(attempt_key, None)
        log_event(
            LOGGER,
            logging.INFO,
            "authentication_succeeded",
            request_id=self.correlation_id(),
            actor_id=user.id,
            organization_id=user.organization_id,
            action="signin",
            result="success",
        )
        self.create_session(user)

    def handle_demo_sign_in(self):
        if not self.accounts.allow_demo:
            raise ResourceNotFound("Demo sign-in is not enabled.")
        user = self.accounts.get("demo-owner")
        if user is None:
            raise ValueError("Demo account is not available.")
        self.ensure_demo_workspace(user)
        self.create_session(user)

    def handle_registration(self, body: dict):
        if self.web_config.registration_mode != "open":
            raise AccessDenied("Workspace registration is not available.")
        if self.database_runtime is None:
            raise ApiError(
                "Workspace registration requires the PostgreSQL runtime.",
                HTTPStatus.SERVICE_UNAVAILABLE,
            )

        attempt_key = self.client_ip()
        cutoff = time.time() - 600
        attempts = [
            attempt
            for attempt in self.registration_attempts.get(attempt_key, [])
            if attempt >= cutoff
        ]
        if len(attempts) >= 5:
            log_event(
                LOGGER,
                logging.WARNING,
                "registration_throttled",
                request_id=self.correlation_id(),
                action="register",
                result="throttled",
            )
            raise ApiError("Too many registration attempts. Try again later.", 429)
        attempts.append(time.time())
        self.registration_attempts[attempt_key] = attempts

        values = {
            field: body.get(field, "")
            for field in ("name", "email", "password", "organization_name")
        }
        if any(not isinstance(value, str) for value in values.values()):
            raise ValueError("Registration fields must be strings.")
        if body.get("accept_terms") is not True:
            raise ValueError(
                "Accept the Terms and Privacy Policy to create a workspace."
            )

        user = self.database_runtime.register_workspace(**values)
        self._request_actor = user
        log_event(
            LOGGER,
            logging.INFO,
            "workspace_registered",
            request_id=self.correlation_id(),
            actor_id=user.id,
            organization_id=user.organization_id,
            action="register",
            result="success",
        )
        self.create_session(user, status=HTTPStatus.CREATED)

    def create_session(
        self,
        user: UserAccount,
        *,
        status: HTTPStatus = HTTPStatus.OK,
    ):
        max_age = self.web_config.session_max_age_seconds
        if self.database_runtime is not None:
            token = self.database_runtime.create_session(user, max_age)
        else:
            token = secrets.token_urlsafe(32)
            self.sessions[token] = {
                "user_id": user.id,
                "expires_at": time.time() + max_age,
            }
        secure = "; Secure" if self.web_config.secure_cookie else ""
        expires = email.utils.formatdate(time.time() + max_age, usegmt=True)
        self.send_json(
            {"state": self.state_payload(user)},
            status=status,
            headers={
                "Set-Cookie": (
                    f"salamandra_session={token}; Path=/; HttpOnly; SameSite=Lax; "
                    f"Max-Age={max_age}; Expires={expires}{secure}"
                )
            },
        )

    def handle_sign_out(self):
        user = self.current_user()
        if user is not None:
            self._request_actor = user
        token = self.session_token()
        if token:
            if self.database_runtime is not None:
                self.database_runtime.revoke_session(token)
            else:
                self.sessions.pop(token, None)
            log_event(
                LOGGER,
                logging.INFO,
                "session_revoked",
                request_id=self.correlation_id(),
                actor_id=getattr(getattr(self, "_request_actor", None), "id", None),
                organization_id=getattr(
                    getattr(self, "_request_actor", None), "organization_id", None
                ),
                action="signout",
                result="success",
            )
        secure = "; Secure" if self.web_config.secure_cookie else ""
        self.send_json(
            {"state": self.state_payload(None)},
            headers={
                "Set-Cookie": (
                    "salamandra_session=; Path=/; HttpOnly; Max-Age=0; "
                    f"Expires=Thu, 01 Jan 1970 00:00:00 GMT; SameSite=Lax{secure}"
                )
            },
        )

    def create_item_from_body(self, body: dict) -> ItemNode:
        requirements = list(
            normalize_dependency_requirements(body.get("requirements", []))
        )

        return ItemNode(
            id=body.get("id", ""),
            type=body.get("type") or None,
            count=self.required_amount(body),
            req=requirements,
            info=body.get("info", ""),
            class_id=body.get("class_id", ""),
            manufacturer=body.get("manufacturer", ""),
            model=body.get("model", ""),
            condition=body.get("condition", "ready"),
            capabilities=tuple(body.get("capabilities", [])),
            connectors=tuple(body.get("connectors", [])),
            attributes=dict(body.get("attributes", {})),
            quality_score=int(body.get("quality_score", 0)),
            preference_score=int(body.get("preference_score", 0)),
            weight_kg=float(body.get("weight_kg", 0)),
        )

    @staticmethod
    def validate_inventory_metadata_access(body: dict, user: UserAccount) -> None:
        forbidden = ADVANCED_INVENTORY_FIELDS.intersection(body)
        if forbidden:
            require_permission(user.role, Permission.INVENTORY_DEFINITION_MANAGE)

    def validate_local_item_dependencies(
        self,
        item: ItemNode,
        scope: str,
        user: UserAccount,
        *,
        replaced_item_id: str | None = None,
    ) -> None:
        if self.database_runtime is not None:
            return
        nodes: list[DependencyNode] = []
        for candidate_scope, owner_user_id in (
            (SHARED_SCOPE, None),
            (PERSONAL_SCOPE, user.id),
        ):
            if scope == SHARED_SCOPE and candidate_scope == PERSONAL_SCOPE:
                continue
            inventory = self.workspace.inventory_for(
                candidate_scope,
                owner_user_id,
                user.organization_id,
            )
            nodes.extend(
                DependencyNode(
                    scope=candidate_scope,
                    owner_user_id=owner_user_id,
                    item_id=candidate.id,
                    requirements=tuple(candidate.req),
                )
                for candidate in inventory.list_items()
            )
        owner_user_id = user.id if scope == PERSONAL_SCOPE else None
        removed = []
        if replaced_item_id:
            normalized_replaced_id = str(replaced_item_id).strip().lower()
            if normalized_replaced_id and normalized_replaced_id != item.id:
                removed.append((scope, owner_user_id, normalized_replaced_id))
        validate_dependency_updates(
            nodes,
            {(scope, owner_user_id, item.id): tuple(item.req)},
            removed_keys=removed,
        )

    def create_item_class_from_body(
        self,
        body: dict,
        organization_id: str,
    ) -> ConfiguredItemClass:
        return ConfiguredItemClass(
            id=body.get("id", ""),
            name=body.get("name", ""),
            family=body.get("family", "other"),
            capabilities=tuple(body.get("capabilities", [])),
            organization_id=organization_id,
            aliases=tuple(body.get("aliases", [])),
            connectors=tuple(body.get("connectors", [])),
            dependency_rules=tuple(
                DependencyRule.from_dict(rule)
                for rule in body.get("dependency_rules", [])
            ),
            substitute_class_ids=tuple(body.get("substitute_class_ids", [])),
            preference_weight=int(body.get("preference_weight", 50)),
            spare_factor=float(body.get("spare_factor", 0)),
            description=body.get("description", ""),
        )

    def build_event_plan(self, body: dict, user: UserAccount):
        requests = [
            Requirement(
                item_id=item.get("item_id", ""),
                amount=int(item.get("amount", 1)),
            )
            for item in body.get("items", [])
            if item.get("item_id")
        ]
        if not requests:
            raise ValueError("Event plan needs at least one requested item.")

        return EventPlanner(
            self.workspace.combined_inventory(user.id, user.organization_id),
            self.catalog,
            reserved_counts=self.memory.active_reservations(
                body.get("event_id"),
                organization_id=user.organization_id,
            ),
            item_classes=self.item_classes,
            organization_id=user.organization_id,
        ).build_plan(requests)

    def plan_request_from_template(self, body: dict, user: UserAccount | None = None) -> dict:
        source_type = body.get("source_type", "template")
        source_id = body.get("source_id", "")
        source = (
            self.kit_source(source_id, user)
            if source_type == "kit"
            else self.templates.get_template(source_id)
        )
        if source is None:
            raise ValueError("Template or kit was not found.")
        return {"items": [item.to_dict() for item in source.items]}

    def kit_source(self, source_id: str, user: UserAccount | None):
        if user is not None:
            stored = self.kits.get(source_id, user.organization_id)
            if stored is not None:
                return stored
        return self.templates.get_kit(source_id)

    def event_from_body(self, body: dict, user: UserAccount) -> EventRecord:
        event_id = self.required_identifier(body.get("event_id"), "event_id")
        event = self.memory.get_for_organization(event_id, user.organization_id)
        if event is None:
            raise ResourceNotFound("Event was not found.")
        return event

    def checkout_kit(
        self,
        body: dict,
        user: UserAccount,
    ) -> tuple[EventRecord, bool]:
        source_id = self.required_identifier(body.get("source_id"), "source_id")
        request_key = self.required_idempotency_key(body.get("idempotency_key"))
        movement_key = f"kit:{request_key}"

        if self.database_runtime is not None:
            def build_authoritative_event() -> dict:
                source = self.kit_source(source_id, user)
                if source is None:
                    raise ResourceNotFound("Kit was not found.")
                plan = self.build_event_plan(
                    {"items": [item.to_dict() for item in source.items]},
                    user,
                )
                if not plan.is_ready:
                    raise StateConflict(
                        "Cannot check out this kit while stock is missing or conflicted."
                    )
                event = EventRecord(
                    title=f"{source.name} checkout",
                    description=f"Manual checkout of the {source.name} kit.",
                    start_date=time.strftime("%Y-%m-%d"),
                    source_type="kit",
                    source_id=source.id,
                    organization_id=user.organization_id,
                    owner_id=user.id,
                    assigned_user_ids=[user.id],
                    requested_items=list(source.items),
                    plan=plan.to_dict(),
                )
                event.prepare_operations(user.id)
                return event.to_dict()

            return self.database_runtime.checkout_kit(
                source_id,
                request_key,
                user,
                self.request_id(),
                build_authoritative_event,
            )

        with self.operation_lock:
            existing = self.memory.get_by_movement_key(
                movement_key,
                user.organization_id,
            )
            if existing is not None:
                if existing.source_type != "kit" or existing.source_id != source_id:
                    raise StateConflict(
                        "This idempotency key was already used for another checkout."
                    )
                return existing, False

            source = self.kit_source(source_id, user)
            if source is None:
                raise ResourceNotFound("Kit was not found.")
            plan = self.build_event_plan(
                {"items": [item.to_dict() for item in source.items]},
                user,
            )
            if not plan.is_ready:
                raise StateConflict(
                    "Cannot check out this kit while stock is missing or conflicted."
                )

            event = EventRecord(
                title=f"{source.name} checkout",
                description=f"Manual checkout of the {source.name} kit.",
                start_date=time.strftime("%Y-%m-%d"),
                source_type="kit",
                source_id=source.id,
                organization_id=user.organization_id,
                owner_id=user.id,
                assigned_user_ids=[user.id],
                requested_items=list(source.items),
                plan=plan.to_dict(),
            )
            event.prepare_operations(user.id)
            operations = EventOperations(self.workspace)
            operations.transition(event, "confirmed", user)
            for item in event.checklist:
                item["done"] = True
            operations.transition(event, "packed", user)
            operations.transition(
                event,
                "out",
                user,
                idempotency_key=movement_key,
            )
            self.memory.add(event)
            self.save_workspace()
            return event, True

    def create_event_from_request(
        self,
        body: dict,
        user: UserAccount,
    ) -> EventRecord:
        description, overrides = self.event_request_parts(body)
        return self.build_event_from_description(description, overrides, user)

    def event_request_parts(
        self,
        body: dict,
        *,
        reject_ids: bool = True,
        fallback_event: EventRecord | None = None,
    ) -> tuple[str, dict]:
        client_event = body.get("event", {})
        if client_event is not None and not isinstance(client_event, dict):
            raise ValueError("Event data must be an object.")
        client_event = client_event or {}

        supplied_ids = (body.get("id"), body.get("event_id"), client_event.get("id"))
        if reject_ids and any(value not in (None, "") for value in supplied_ids):
            raise ValueError(
                "Client-selected event IDs are not accepted for event creation."
            )

        fallback_description = fallback_event.description if fallback_event else ""
        description = body.get(
            "description",
            client_event.get("description", fallback_description),
        )
        if not isinstance(description, str) or not description.strip():
            raise ValueError("Event description is required.")
        if len(description) > 20_000:
            raise ValueError("Event description is too long.")

        overrides = body.get("overrides")
        if overrides is None:
            fallback_values = (
                {
                    "title": fallback_event.title,
                    "start_date": fallback_event.start_date,
                    "start_time": fallback_event.start_time,
                    "location": fallback_event.location,
                    "duration_minutes": fallback_event.duration_minutes,
                    "attendee_count": fallback_event.attendee_count,
                }
                if fallback_event
                else {}
            )
            overrides = {
                name: client_event.get(name, fallback_values.get(name))
                for name in (
                    "title",
                    "start_date",
                    "start_time",
                    "location",
                    "duration_minutes",
                    "attendee_count",
                    "planning_mode",
                    "capability_requirements",
                    "assigned_user_ids",
                )
                if name in client_event or name in fallback_values
            }
        if not isinstance(overrides, dict):
            raise ValueError("Event overrides must be an object.")
        if fallback_event is not None:
            overrides = {
                "title": fallback_event.title,
                "start_date": fallback_event.start_date,
                "start_time": fallback_event.start_time,
                "location": fallback_event.location,
                "duration_minutes": fallback_event.duration_minutes,
                "attendee_count": fallback_event.attendee_count,
                "assigned_user_ids": list(fallback_event.assigned_user_ids),
                **overrides,
            }
        allowed_overrides = {
            name: overrides[name]
            for name in (
                "title",
                "start_date",
                "start_time",
                "location",
                "duration_minutes",
                "attendee_count",
                "planning_mode",
                "capability_requirements",
                "assigned_user_ids",
            )
            if name in overrides
        }
        return description.strip(), allowed_overrides

    def build_event_from_description(
        self,
        description: str,
        overrides: dict,
        user: UserAccount,
        *,
        exclude_event_id: str | None = None,
        inventory_user_id: str | None = None,
        optimize_overlaps: bool = True,
        owner_user_id: str | None = None,
    ) -> EventRecord:
        draft = EventDescriptionPlanner(
            self.workspace.combined_inventory(
                inventory_user_id or user.id, user.organization_id
            ),
            self.catalog,
            self.memory,
            organization_id=user.organization_id,
            item_classes=self.item_classes,
            exclude_event_id=exclude_event_id,
            optimize_overlaps=optimize_overlaps,
        ).draft_from_description(description, overrides=overrides)
        event = draft.record
        event.organization_id = user.organization_id
        event.owner_id = owner_user_id or user.id
        event.assigned_user_ids = self.valid_event_assignees(
            list(overrides.get("assigned_user_ids", [])),
            user,
            event.owner_id,
        )
        event.status = "planning"
        event.history = []
        event.movements = []
        return event

    def valid_event_assignees(
        self,
        requested_ids: list[str] | tuple[str, ...],
        user: UserAccount,
        owner_user_id: str | None = None,
    ) -> list[str]:
        if not isinstance(requested_ids, (list, tuple)):
            raise ValueError("Event crew must be a list of workspace members.")
        ordered = list(
            dict.fromkeys(
                [owner_user_id or user.id, *(str(value).strip() for value in requested_ids)]
            )
        )
        if len(ordered) > 100 or any(not value for value in ordered):
            raise ValueError("Event crew selection is invalid.")
        active_ids = {
            account.id
            for account in self.accounts.list_organization_users(user.organization_id)
        }
        if not set(ordered).issubset(active_ids):
            raise ValueError(
                "Choose only active members of this workspace for the event crew."
            )
        return ordered

    def inventory_scope(self, body: dict) -> str:
        scope = body.get("scope", SHARED_SCOPE)
        if scope not in {SHARED_SCOPE, PERSONAL_SCOPE}:
            raise ValueError("Inventory scope must be shared or personal.")
        return scope

    def authorized_inventory_scope(self, body: dict, user: UserAccount) -> str:
        scope = self.inventory_scope(body)
        permission = (
            Permission.INVENTORY_PERSONAL_WRITE
            if scope == PERSONAL_SCOPE
            else Permission.INVENTORY_SHARED_WRITE
        )
        require_permission(user.role, permission)
        return scope

    def state_payload(self, user: UserAccount | None) -> dict:
        if user is None:
            inventories = self.workspace.to_dict(None, None)
            return {
                "auth": {
                    "authenticated": False,
                    "user": None,
                    "users": [],
                    "demo_available": self.accounts.allow_demo,
                    "registration_mode": self.web_config.registration_mode,
                },
                "organization": {},
                "presence": [],
                "inventory": inventories["combined"],
                "inventories": inventories,
                "presets": {"presets": [], "tags": []},
                "templates": {
                    "templates": [],
                    "kits": [],
                    "suggested_templates": [],
                    "suggested_kits": [],
                },
                "item_classes": {"classes": []},
                "integrations": {},
                "events": {
                    "events": [],
                    "learning_count": 0,
                    "active_reservations": {},
                },
                "google_calendar": {
                    "status": "not_connected",
                    "calendar_id": "",
                    "write_requires_approval": True,
                },
                "sync": self.sync_payload(None),
            }
        organization_users = (
            self.accounts.list_organization_users(user.organization_id)
            if user
            else []
        )
        active_user_ids = (
            self.database_runtime.active_user_ids(user.organization_id)
            if self.database_runtime is not None
            else {
                str(session.get("user_id", ""))
                for session in self.sessions.values()
                if float(session.get("expires_at", 0)) > time.time()
            }
        )
        auth_payload = {
            "authenticated": user is not None,
            "user": user.to_public_dict() if user else None,
            "users": [account.to_public_dict() for account in organization_users],
            "demo_available": self.accounts.allow_demo,
            "registration_mode": self.web_config.registration_mode,
        }
        inventories = self.workspace.to_dict(
            user.id if user else None,
            user.organization_id if user else None,
        )
        events = self.memory.to_dict(user.organization_id if user else None)
        suggestion_catalog = self.templates.to_dict()
        return {
            "auth": auth_payload,
            "organization": self.organization_payload(user, organization_users),
            "presence": [
                {
                    **account.to_public_dict(),
                    "online": account.id in active_user_ids,
                }
                for account in organization_users
            ],
            "inventory": inventories["combined"],
            "inventories": inventories,
            "presets": self.catalog.to_dict(),
            "templates": {
                "templates": [],
                "kits": [kit.to_dict() for kit in self.kits.list_kits(user.organization_id)],
                "suggested_templates": suggestion_catalog["templates"],
                "suggested_kits": suggestion_catalog["kits"],
            },
            "item_classes": self.item_classes.to_dict(
                user.organization_id if user else None
            ),
            "integrations": (
                self.integrations.get_all(user.organization_id)
                if user
                else {}
            ),
            "events": events,
            "google_calendar": {
                "status": "not_connected",
                "calendar_id": "primary",
                "write_requires_approval": True,
            },
            "sync": self.sync_payload(user),
        }

    def organization_payload(
        self,
        user: UserAccount | None,
        users: list[UserAccount],
    ) -> dict:
        if user is None:
            return {}
        return {
            "id": user.organization_id,
            "name": user.organization_name,
            "plan": "",
            "seat_count": len(users),
            "seat_limit": 0,
            "warehouse": user.warehouse,
        }

    def sync_payload(self, user: UserAccount | None) -> dict:
        if user is None:
            return {
                "status": "signed_out",
                "account_id": "",
                "google_calendar": "not_connected",
                "pending_events": [],
                "last_sync": "",
                "note": "Sign in to view synchronization status.",
            }
        pending = [
            event.id
            for event in self.memory.list_events(user.organization_id)
            if event.sync_status in {"local", "pending"}
        ]
        return {
            "status": "local",
            "account_id": user.id,
            "google_calendar": "not_connected",
            "pending_events": pending,
            "last_sync": "",
            "note": "Google Calendar writes are prepared but not connected.",
        }

    def ensure_demo_workspace(self, user: UserAccount):
        shared = self.workspace.inventory_for(
            SHARED_SCOPE,
            user.id,
            user.organization_id,
        )
        if not shared.list_items():
            demo_stock = [
                ("pa-speaker", 8),
                ("ev-zlx-15p", 2),
                ("pro-tech-15", 4),
                ("stage-monitor", 8),
                ("di-box", 6),
                ("sm58-mic", 12),
                ("xlr-10m", 36),
                ("mic-stand", 10),
                ("small-mixer", 3),
                ("led-par", 16),
                ("dmx-cable", 12),
                ("power-cable", 24),
                ("power-distro-63a", 3),
                ("stacking-case", 8),
            ]
            for preset_id, amount in demo_stock:
                self.workspace.add_from_preset(
                    self.catalog.get(preset_id),
                    SHARED_SCOPE,
                    user.id,
                    amount=amount,
                    organization_id=user.organization_id,
                )

        demo_minimums = [
            ("ev-zlx-15p", "ev zlx-15p", 2),
            ("pro-tech-15", "pro tech 15", 4),
            ("stage-monitor", "powered stage monitor", 8),
            ("di-box", "di box", 6),
            ("standard-estate-car", "standard estate car", 1),
            ("cargo-van", "cargo van", 2),
            ("utility-cart", "utility cart", 5),
            ("folding-table", "folding table", 12),
            ("folding-chair", "folding chair", 80),
            ("crowd-barrier", "crowd barrier", 20),
            ("pop-up-canopy", "pop-up canopy", 4),
            ("presentation-projector", "presentation projector", 3),
            ("projection-screen", "projection screen", 3),
            ("mobile-catering-station", "mobile catering station", 4),
        ]
        for preset_id, item_id, minimum in demo_minimums:
            existing = shared.get_item(item_id)
            current_total = (existing.count + existing.in_use_count) if existing else 0
            if current_total < minimum:
                self.workspace.add_from_preset(
                    self.catalog.get(preset_id),
                    SHARED_SCOPE,
                    user.id,
                    amount=minimum - current_total,
                    organization_id=user.organization_id,
                )

        personal = self.workspace.inventory_for(
            PERSONAL_SCOPE,
            user.id,
            user.organization_id,
        )
        if not personal.list_items():
            personal_items = [
                ItemNode(
                    "Sennheiser HD 25",
                    "accessory",
                    info="Maya's monitoring headphones; stored in the black personal case.",
                ),
                ItemNode(
                    "RF Explorer",
                    "tool",
                    info="Portable spectrum analyzer for RF coordination and site checks.",
                ),
            ]
            for item in personal_items:
                self.workspace.add_item(
                    item,
                    1,
                    PERSONAL_SCOPE,
                    user.id,
                    user.organization_id,
                )

        if not self.memory.list_events(user.organization_id):
            descriptions = [
                (
                    "Orion Tech Summit",
                    "Conference for 90 people on 2026-08-27 at 09:00, talks and panels at Hangar 11.",
                    ["demo-owner", "demo-tech", "demo-producer"],
                ),
                (
                    "Atlas Brand Launch",
                    "Brand presentation for 140 guests on 2026-08-29 at 18:30 with speeches, lights and PA at Riverside Hall.",
                    ["demo-producer", "demo-tech"],
                ),
            ]
            for title, description, assigned_users in descriptions:
                draft = EventDescriptionPlanner(
                    self.workspace.combined_inventory(user.id, user.organization_id),
                    self.catalog,
                    self.memory,
                    organization_id=user.organization_id,
                    item_classes=self.item_classes,
                ).draft_from_description(description)
                draft.record.title = title
                draft.record.owner_id = user.id
                draft.record.assigned_user_ids = assigned_users
                draft.record.status = "confirmed"
                draft.record.prepare_operations(user.id)
                self.memory.add(draft.record)

        self.save_workspace()

    def save_workspace(self):
        self.workspace.save()

    def apply_allocation_updates(self, event: EventRecord, user: UserAccount):
        updates = list(event.plan.get("allocation_updates", []))
        for update in updates:
            affected = self.memory.get_for_organization(
                update.get("event_id", ""),
                user.organization_id,
            )
            if affected is None:
                continue
            if affected.status not in {"planning", "confirmed"}:
                continue
            affected.plan = dict(update.get("plan", affected.plan))
            affected.checklist = []
            affected.return_checklist = []
            affected.prepare_operations(user.id)
            affected.add_history(
                "reallocated",
                user.id,
                f"Gear rebalanced after approving overlapping event {event.title}.",
            )
            if self.database_runtime is not None:
                if affected.status != "planning":
                    continue
                self.database_runtime.update_planning_plan(
                    affected,
                    user,
                    self.request_id(),
                    f"Gear rebalanced after approving overlapping event {event.title}.",
                )
            else:
                self.memory.add(affected)
        event.plan["allocation_updates"] = []

    def export_inventory_csv(self, user: UserAccount):
        output = io.StringIO(newline="")
        fieldnames = [
            "id", "type", "count", "in_use_count", "class_id", "manufacturer",
            "model", "condition", "quality_score", "preference_score", "weight_kg",
            "capabilities", "connectors", "info",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for item in self.workspace.combined_inventory(
            user.id,
            user.organization_id,
        ).list_items():
            row = {
                    "id": item.id,
                    "type": item.type.value if item.type else "other",
                    "count": item.count,
                    "in_use_count": item.in_use_count,
                    "class_id": item.class_id,
                    "manufacturer": item.manufacturer,
                    "model": item.model,
                    "condition": item.condition,
                    "quality_score": item.quality_score,
                    "preference_score": item.preference_score,
                    "weight_kg": item.weight_kg,
                    "capabilities": ";".join(item.capabilities),
                    "connectors": ";".join(item.connectors),
                    "info": item.info,
                }
            writer.writerow(
                {
                    name: self.spreadsheet_safe_value(value)
                    for name, value in row.items()
                }
            )
        payload = output.getvalue().encode("utf-8-sig")
        self.integrations.record_transfer(user.organization_id, "exported")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header(
            "Content-Disposition",
            'attachment; filename="salamandra-inventory.csv"',
        )
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_security_headers()
        self.end_headers()
        self.wfile.write(payload)

    def import_inventory_csv(self, body: dict, user: UserAccount) -> int:
        csv_text = str(body.get("csv", ""))
        if not csv_text.strip():
            raise ValueError("Choose a non-empty CSV file to import.")
        reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")))
        if not reader.fieldnames:
            raise ValueError("Inventory CSV must include a header row.")
        mapping = body.get("column_mapping", {})
        if not isinstance(mapping, dict):
            raise ValueError("CSV column mapping must be an object.")
        headers = {str(header).strip().casefold(): str(header) for header in reader.fieldnames}

        def source(name: str, *aliases: str) -> str | None:
            configured = str(mapping.get(name, "")).strip()
            if configured:
                if configured not in reader.fieldnames:
                    raise ValueError(f"Mapped CSV column {configured} was not found.")
                return configured
            return next((headers[value.casefold()] for value in (name, *aliases) if value.casefold() in headers), None)

        columns = {
            "name": source("name", "item", "equipment", "id"),
            "count": source("count", "quantity", "qty"),
            "type": source("type", "category", "department"),
            "info": source("info", "description", "notes"),
            "class_id": source("class_id", "class"),
            "manufacturer": source("manufacturer", "brand"),
            "model": source("model"),
            "condition": source("condition", "status"),
            "capabilities": source("capabilities", "capability"),
            "connectors": source("connectors", "connector"),
            "weight_kg": source("weight_kg", "weight"),
        }
        if columns["name"] is None:
            raise ValueError("Map a CSV column to Item name before importing.")

        scope = self.authorized_inventory_scope(body, user)
        inventory = self.workspace.inventory_for(scope, user.id, user.organization_id)
        staged_by_id = {
            item.id: ItemNode.from_dict(item.to_dict())
            for item in inventory.list_items()
        }
        seen_ids: set[str] = set()
        for row_number, row in enumerate(reader, start=2):
            if row_number > 10_001:
                raise ValueError("Inventory CSV cannot contain more than 10,000 rows.")
            if any(len(str(value or "")) > 4_096 for value in row.values()):
                raise ValueError(f"CSV row {row_number} contains a field that is too long.")
            item_name = str(row.get(columns["name"] or "", "")).strip()
            if not item_name:
                continue
            item_id = (
                item_name
                if (columns["name"] or "").casefold() == "id"
                else self.csv_item_id(item_name)
            )
            normalized_id = item_id.casefold()
            if normalized_id in seen_ids:
                raise ValueError(f"Inventory CSV contains duplicate item name {item_name}.")
            seen_ids.add(normalized_id)
            count = int(row.get(columns["count"] or "", "") or 0)
            if count < 0:
                raise ValueError(f"Count cannot be negative for {item_name}.")
            existing = inventory.get_item(item_id)
            item = ItemNode(
                id=item_id,
                type=row.get(columns["type"] or "", "") or (existing.type if existing else "other"),
                count=count,
                info=str(row.get(columns["info"] or "", "")),
                class_id=str(row.get(columns["class_id"] or "", "")),
                manufacturer=str(row.get(columns["manufacturer"] or "", "")),
                model=str(row.get(columns["model"] or "", "")),
                condition=str(row.get(columns["condition"] or "", "") or "ready"),
                capabilities=tuple(
                    value.strip()
                    for value in str(row.get(columns["capabilities"] or "", "")).split(";")
                    if value.strip()
                ),
                connectors=tuple(
                    value.strip()
                    for value in str(row.get(columns["connectors"] or "", "")).split(";")
                    if value.strip()
                ),
                quality_score=existing.quality_score if existing else 70,
                preference_score=existing.preference_score if existing else 70,
                weight_kg=float(row.get(columns["weight_kg"] or "", "") or 0),
                in_use_count=existing.in_use_count if existing else 0,
            )
            staged_by_id[item.id] = item

        if not seen_ids:
            raise ValueError("The CSV did not contain any inventory rows.")
        staged = list(staged_by_id.values())
        self.workspace.replace_items(
            staged,
            scope,
            user.id,
            user.organization_id,
            request_id=self.operation_request_id(body),
            reason_code=str(body.get("reason_code", "csv_reconciliation")),
            reason=str(body.get("reason", "Inventory reconciled from CSV.")),
            source="csv_import",
        )
        self.save_workspace()
        self.integrations.record_transfer(user.organization_id, "imported")
        return len(seen_ids)

    def inventory_csv_preview(self, body: dict) -> dict:
        csv_text = str(body.get("csv", ""))
        if not csv_text.strip():
            raise ValueError("Choose a non-empty CSV file to preview.")
        reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")))
        if not reader.fieldnames:
            raise ValueError("Inventory CSV must include a header row.")
        rows = []
        for index, row in enumerate(reader):
            if index >= 5:
                break
            rows.append({str(key): str(value or "") for key, value in row.items()})
        headers = [str(header) for header in reader.fieldnames]
        folded = {header.casefold(): header for header in headers}
        aliases = {
            "name": ("name", "item", "equipment", "id"),
            "count": ("count", "quantity", "qty"),
            "type": ("type", "category", "department"),
            "info": ("info", "description", "notes"),
            "manufacturer": ("manufacturer", "brand"),
            "model": ("model",),
            "condition": ("condition", "status"),
        }
        suggested = {
            field: next((folded[name] for name in names if name in folded), "")
            for field, names in aliases.items()
        }
        return {"headers": headers, "rows": rows, "suggested_mapping": suggested}

    @staticmethod
    def csv_item_id(name: str) -> str:
        normalized = "-".join(
            part for part in "".join(
                character.lower() if character.isalnum() else " " for character in name
            ).split() if part
        )
        return normalized[:96] or secrets.token_hex(8)

    def current_user(self) -> UserAccount | None:
        token = self.session_token()
        if not token:
            return None
        if self.database_runtime is not None:
            user = self.database_runtime.current_user(token)
            if user is None:
                self.log_invalid_session()
            return user
        session = self.sessions.get(token)
        if not session:
            self.log_invalid_session()
            return None
        if float(session.get("expires_at", 0)) <= time.time():
            self.sessions.pop(token, None)
            self.log_invalid_session()
            return None
        user_id = str(session.get("user_id", ""))
        user = self.accounts.get(user_id) if user_id else None
        if user is None:
            self.log_invalid_session()
        return user

    def require_user(self) -> UserAccount:
        user = self.current_user()
        if user is None:
            raise AuthenticationRequired()
        self._request_actor = user
        return user

    def session_token(self) -> str:
        cookie_header = self.headers.get("Cookie", "")
        if not cookie_header:
            return ""
        cookie = SimpleCookie()
        cookie.load(cookie_header)
        morsel = cookie.get("salamandra_session")
        return morsel.value if morsel else ""

    def read_json_body(self) -> dict:
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            return {}
        if content_length < 0 or content_length > MAX_JSON_BODY_BYTES:
            raise ApiError("Request body is too large.", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)

        raw_body = self.rfile.read(content_length)
        body = json.loads(raw_body.decode("utf-8"))
        if not isinstance(body, dict):
            raise ValueError("Request body must be a JSON object.")
        return body

    def serve_static_file(self, request_path: str):
        path = Path(request_path.lstrip("/"))
        if request_path == "/":
            path = Path("index.html")

        file_path = (WEB_DIR / path).resolve()
        if WEB_DIR.resolve() not in file_path.parents:
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        if not file_path.is_file() and not path.suffix:
            file_path = WEB_DIR / "index.html"

        if not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        content_type, _ = mimetypes.guess_type(file_path)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_security_headers()
        self.send_header(
            "Cache-Control",
            "no-cache" if file_path.name == "index.html" else "public, max-age=31536000, immutable",
        )
        self.end_headers()

        with open(file_path, "rb") as file:
            self.wfile.write(file.read())

    def send_json(
        self,
        payload: dict,
        status: HTTPStatus = HTTPStatus.OK,
        headers: dict[str, str] | None = None,
    ):
        encoded_payload = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded_payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_security_headers()
        self.send_cors_headers()
        self.send_header("X-Request-ID", self.correlation_id())
        for header, value in (headers or {}).items():
            self.send_header(header, value)
        self.end_headers()
        self.wfile.write(encoded_payload)

    def send_json_error(self, message: str, status: HTTPStatus):
        self.send_json({"error": message}, status)

    def required_amount(self, body: dict) -> int:
        amount = int(body.get("amount", 1))
        if amount <= 0:
            raise ValueError("Amount must be greater than zero.")
        return amount

    def optional_int(self, value) -> int | None:
        if value in (None, ""):
            return None
        return int(value)

    def required_identifier(self, value, field_name: str) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{field_name} must be a string.")
        normalized = value.strip().lower()
        if not normalized or len(normalized) > 128:
            raise ValueError(f"{field_name} is invalid.")
        return normalized

    def required_idempotency_key(self, value) -> str:
        if not isinstance(value, str):
            raise ValueError("idempotency_key must be a string.")
        normalized = value.strip()
        if (
            not normalized
            or len(normalized) > 160
            or any(ord(character) < 33 or ord(character) > 126 for character in normalized)
        ):
            raise ValueError("idempotency_key is invalid.")
        return normalized

    def request_id(self) -> str:
        supplied = self.headers.get("Idempotency-Key", "").strip()
        if supplied:
            return self.required_idempotency_key(supplied)
        return secrets.token_urlsafe(18)

    def correlation_id(self) -> str:
        cached = getattr(self, "_correlation_id", "")
        if cached:
            return cached
        supplied = self.headers.get("X-Request-ID", "").strip()
        if (
            supplied
            and len(supplied) <= 128
            and all(character.isalnum() or character in "-_.:" for character in supplied)
        ):
            self._correlation_id = supplied
        else:
            self._correlation_id = secrets.token_urlsafe(18)
        return self._correlation_id

    def operation_request_id(self, body: dict) -> str:
        supplied = body.get("idempotency_key")
        if supplied not in (None, ""):
            return self.required_idempotency_key(supplied)
        return self.request_id()

    def validate_request_host(self) -> None:
        raw_host = self.headers.get("Host", "")
        try:
            host = normalize_host(raw_host, "Host")
        except ConfigurationError as error:
            raise ApiError("Request host is invalid.", HTTPStatus.BAD_REQUEST) from error
        if host not in self.web_config.trusted_hosts:
            raise ApiError("Request host is not trusted.", HTTPStatus.BAD_REQUEST)

    def validate_request_origin(self, *, require: bool):
        raw_origin = self.headers.get("Origin", "").strip()
        if not raw_origin:
            if require:
                raise AccessDenied("An approved request origin is required.")
            return
        try:
            origin = normalize_origin(raw_origin, "Origin")
        except ConfigurationError as error:
            raise AccessDenied("Cross-origin requests are not allowed.") from error
        if origin not in self.web_config.allowed_origins:
            raise AccessDenied("Cross-origin requests are not allowed.")

    def send_cors_headers(self) -> None:
        raw_origin = self.headers.get("Origin", "").strip()
        if not raw_origin:
            return
        try:
            origin = normalize_origin(raw_origin, "Origin")
        except ConfigurationError:
            return
        if origin in self.web_config.allowed_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")

    def client_ip(self) -> str:
        direct = str(self.client_address[0])
        if (
            not self.web_config.trust_proxy_headers
            or direct not in self.web_config.trusted_proxy_ips
        ):
            return direct
        forwarded = self.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
        try:
            return str(ipaddress.ip_address(forwarded))
        except ValueError:
            return direct

    def log_invalid_session(self) -> None:
        log_event(
            LOGGER,
            logging.INFO,
            "invalid_session",
            request_id=self.correlation_id(),
            action=urlparse(self.path).path,
            result="unauthenticated",
        )

    def log_api_error(self, error: ApiError, path: str) -> None:
        status = int(error.status)
        if isinstance(error, AccessDenied):
            event_name = "authorization_denied"
        elif isinstance(error, StateConflict) and path.startswith("/api/events"):
            event_name = "event_transition_failed"
        elif isinstance(error, StateConflict) and path.startswith("/api/inventory"):
            event_name = "inventory_adjustment_failed"
        elif isinstance(error, AuthenticationRequired):
            event_name = "authentication_required"
        else:
            event_name = "request_rejected"
        level = logging.WARNING if status in {403, 409, 429} else logging.INFO
        actor = getattr(self, "_request_actor", None)
        log_event(
            LOGGER,
            level,
            event_name,
            request_id=self.correlation_id(),
            actor_id=getattr(actor, "id", None),
            organization_id=getattr(actor, "organization_id", None),
            action=path,
            result=status,
        )

    def spreadsheet_safe_value(self, value):
        if not isinstance(value, str):
            return value
        if value.startswith(("=", "+", "-", "@", "\t", "\r")):
            return f"'{value}"
        return value

    def send_security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'",
        )
        if self.web_config.secure_cookie:
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")

    def log_message(self, format, *args):
        return


def run(host: str = "127.0.0.1", port: int = 8000):
    try:
        web_config = WebConfig.from_environment()
    except ConfigurationError:
        configure_logging("INFO")
        log_event(
            LOGGER,
            logging.ERROR,
            "startup_failed",
            action="configuration",
            result="invalid",
        )
        raise
    configure_logging(web_config.log_level)
    database_url = web_config.database_url
    engine = None
    readiness_probe = None
    if database_url:
        from database import create_database_engine, session_factory
        from postgres_runtime import PostgresRuntime

        engine = create_database_engine(database_url, production=True)
        readiness_probe = DatabaseReadiness(engine, ROOT_DIR)
        if web_config.production_like:
            result = readiness_probe.check()
            if not result.ready:
                log_event(
                    LOGGER,
                    logging.ERROR,
                    "startup_failed",
                    action="database_readiness",
                    result=result.code,
                )
                engine.dispose()
                raise RuntimeError(
                    "PostgreSQL is unavailable or its Alembic schema revision is not current."
                )
        runtime = PostgresRuntime(session_factory(engine))
        SalamandraServer.database_runtime = runtime
        SalamandraServer.accounts = runtime.accounts
        SalamandraServer.workspace = runtime.workspace
        SalamandraServer.memory = runtime.memory
        SalamandraServer.item_classes = runtime.item_classes
        SalamandraServer.integrations = runtime.integrations
        SalamandraServer.kits = runtime.kits
    elif not web_config.json_dev_enabled and not web_config.demo_enabled:
        raise RuntimeError(
            "Set SALAMANDRA_DATABASE_URL for PostgreSQL, or explicitly enable "
            "SALAMANDRA_ALLOW_JSON_DEV=1 for local compatibility storage."
        )
    demo_enabled = web_config.demo_enabled
    if not database_url:
        SalamandraServer.database_runtime = None
        SalamandraServer.accounts = AccountStore(
            USERS_PATH,
            seed_defaults=demo_enabled,
            allow_demo=demo_enabled,
        )
        SalamandraServer.workspace = InventoryWorkspace(WORKSPACE_PATH, INVENTORY_PATH)
        SalamandraServer.kits = KitStore(KITS_PATH)
        SalamandraServer.workspace.migrate_preset_metadata(SalamandraServer.catalog)
        SalamandraServer.memory = EventMemory(EVENTS_PATH)
        SalamandraServer.item_classes = ItemClassCatalog(ITEM_CLASSES_PATH)
        SalamandraServer.integrations = IntegrationStore(INTEGRATIONS_PATH)
    SalamandraServer.sessions = {}
    SalamandraServer.login_attempts = {}
    SalamandraServer.registration_attempts = {}
    SalamandraServer.event_create_requests = {}
    SalamandraServer.operation_lock = threading.RLock()
    SalamandraServer.web_config = web_config
    SalamandraServer.readiness_probe = readiness_probe
    server = ThreadingHTTPServer((host, port), SalamandraServer)
    log_event(
        LOGGER,
        logging.INFO,
        "startup_succeeded",
        action="server_start",
        result="ready" if readiness_probe else "development",
        mode=web_config.mode,
        host=host,
        port=port,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
        if engine is not None:
            engine.dispose()
        log_event(
            LOGGER,
            logging.INFO,
            "shutdown_completed",
            action="server_stop",
            result="success",
            mode=web_config.mode,
        )


def server_bind_address(environment=None) -> tuple[str, int]:
    values = os.environ if environment is None else environment
    if "PORT" not in values:
        return "127.0.0.1", 8000

    raw_port = str(values.get("PORT", "")).strip()
    try:
        port = int(raw_port)
    except ValueError as error:
        raise ConfigurationError(
            "PORT must be an integer between 1 and 65535."
        ) from error
    if port < 1 or port > 65_535:
        raise ConfigurationError("PORT must be an integer between 1 and 65535.")
    return "0.0.0.0", port


if __name__ == "__main__":
    run(*server_bind_address())
