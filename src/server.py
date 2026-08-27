from __future__ import annotations

import csv
import io
import json
import mimetypes
import os
import secrets
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
from integrations import IntegrationStore
from Item_node import ItemNode, Requirement
from item_classes import ConfiguredItemClass, DependencyRule, ItemClassCatalog
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


ROOT_DIR = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT_DIR / "web"
DOCS_DIR = ROOT_DIR / "docs"
INVENTORY_PATH = DOCS_DIR / "inventory.json"
WORKSPACE_PATH = DOCS_DIR / "inventories.json"
EVENTS_PATH = DOCS_DIR / "events.json"
USERS_PATH = DOCS_DIR / "users.json"
ITEM_CLASSES_PATH = DOCS_DIR / "item_classes.json"
INTEGRATIONS_PATH = DOCS_DIR / "integrations.json"
MAX_JSON_BODY_BYTES = 1_000_000


class SalamandraServer(BaseHTTPRequestHandler):
    accounts = AccountStore(USERS_PATH)
    workspace = InventoryWorkspace(WORKSPACE_PATH, INVENTORY_PATH)
    catalog = PresetCatalog()
    templates = TemplateCatalog()
    memory = EventMemory(EVENTS_PATH)
    item_classes = ItemClassCatalog(ITEM_CLASSES_PATH)
    integrations = IntegrationStore(INTEGRATIONS_PATH)
    sessions: dict[str, dict[str, float | str]] = {}
    login_attempts: dict[str, list[float]] = {}

    def do_GET(self):
        try:
            self._do_GET()
        except ApiError as error:
            self.send_json_error(str(error), error.status)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            self.send_json_error(str(error) or "Request is invalid.", HTTPStatus.BAD_REQUEST)

    def _do_GET(self):
        parsed_url = urlparse(self.path)
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
            self.send_json(self.templates.to_dict())
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
            self.validate_request_origin()
            body = self.read_json_body()

            if parsed_url.path == "/api/auth/signin":
                self.handle_sign_in(body)
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
                item = self.create_item_from_body(body)
                stored_item = self.workspace.add_item(
                    item,
                    amount=item.count,
                    scope=scope,
                    user_id=user.id,
                    organization_id=user.organization_id,
                )
                self.save_workspace()
                self.send_json({"item": stored_item.to_dict(), "state": self.state_payload(user)})
                return

            if parsed_url.path == "/api/inventory/items/update":
                scope = self.authorized_inventory_scope(body, user)
                updated_item = self.workspace.update_item(
                    body.get("original_id", body.get("id", "")),
                    self.create_item_from_body(body),
                    scope=scope,
                    user_id=user.id,
                    organization_id=user.organization_id,
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
                )
                self.save_workspace()
                self.send_json({"item": stored_item.to_dict(), "state": self.state_payload(user)})
                return

            if parsed_url.path == "/api/inventory/use":
                scope = self.authorized_inventory_scope(body, user)
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
                plan_request = self.plan_request_from_template(body)
                plan = self.build_event_plan(plan_request, user)
                self.send_json(
                    {
                        "request": plan_request,
                        "plan": plan.to_dict(),
                        "state": self.state_payload(user),
                    }
                )
                return

            if parsed_url.path == "/api/events/use":
                require_permission(user.role, Permission.INVENTORY_SHARED_WRITE)
                plan = self.build_event_plan(body, user)
                if not plan.is_ready:
                    raise ValueError("Cannot use event plan while stock is missing or conflicted.")
                for line in plan.lines:
                    self.workspace.use_from_available_scopes(
                        user.id,
                        line.item_id,
                        line.amount,
                        user.organization_id,
                    )
                self.save_workspace()
                self.send_json({"plan": plan.to_dict(), "state": self.state_payload(user)})
                return

            if parsed_url.path == "/api/events/return":
                require_permission(user.role, Permission.OPERATIONS_RETURN)
                event = self.event_from_body(body, user)
                changed = EventOperations(self.workspace).transition(event, "returned", user)
                if changed:
                    self.save_workspace()
                self.memory.add(event)
                self.send_json({"event": event.to_dict(), "state": self.state_payload(user)})
                return

            if parsed_url.path == "/api/events/checklist":
                event = self.event_from_body(body, user)
                phase = body.get("phase", "pack")
                if phase == "return":
                    require_permission(user.role, Permission.OPERATIONS_RETURN)
                    if event.status != "out":
                        raise StateConflict("Return checklist is available only after dispatch.")
                else:
                    require_permission(user.role, Permission.OPERATIONS_PACK)
                    if event.status not in {"confirmed"}:
                        raise StateConflict("Packing checklist is available only for confirmed events.")
                checklist = event.return_checklist if phase == "return" else event.checklist
                item_id = self.required_identifier(body.get("item_id"), "item_id")
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
                event = self.event_from_body(body, user)
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
                changed = EventOperations(self.workspace).transition(event, next_status, user)
                if changed:
                    if next_status in {"out", "returned"}:
                        self.save_workspace()
                    self.memory.add(event)
                self.send_json({"event": event.to_dict(), "state": self.state_payload(user)})
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
                draft.record.assigned_user_ids = [user.id]
                self.send_json({"draft": draft.to_dict(), "state": self.state_payload(user)})
                return

            if parsed_url.path == "/api/events/save":
                require_permission(user.role, Permission.EVENTS_CREATE)
                event = self.create_event_from_request(body, user)
                self.apply_allocation_updates(event, user)
                event.prepare_operations(user.id)
                self.memory.add(event)
                self.send_json(
                    {"event": event.to_dict(), "state": self.state_payload(user)},
                    status=HTTPStatus.CREATED,
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
            self.send_json_error(str(error), error.status)
        except ValueError as error:
            self.send_json_error(str(error), HTTPStatus.BAD_REQUEST)
        except (json.JSONDecodeError, TypeError, KeyError):
            self.send_json_error("Request body must be valid JSON.", HTTPStatus.BAD_REQUEST)
        except Exception:
            self.send_json_error("Salamandra could not complete that request.", HTTPStatus.INTERNAL_SERVER_ERROR)

    def handle_sign_in(self, body: dict):
        email = body.get("email", "")
        password = body.get("password", "")
        if not isinstance(email, str) or not isinstance(password, str):
            raise ValueError("Email and password must be strings.")
        attempt_key = f"{self.client_address[0]}:{email.strip().lower()}"
        cutoff = time.time() - 300
        attempts = [
            attempt
            for attempt in self.login_attempts.get(attempt_key, [])
            if attempt >= cutoff
        ]
        if len(attempts) >= 5:
            raise ApiError("Too many sign-in attempts. Try again later.", 429)
        user = self.accounts.authenticate(email, password)
        if user is None:
            attempts.append(time.time())
            self.login_attempts[attempt_key] = attempts
            self.send_json_error("Email or password is incorrect.", HTTPStatus.UNAUTHORIZED)
            return

        self.login_attempts.pop(attempt_key, None)
        self.create_session(user)

    def handle_demo_sign_in(self):
        if not self.accounts.allow_demo:
            raise ResourceNotFound("Demo sign-in is not enabled.")
        user = self.accounts.get("demo-owner")
        if user is None:
            raise ValueError("Demo account is not available.")
        self.ensure_demo_workspace(user)
        self.create_session(user)

    def create_session(self, user: UserAccount):
        token = secrets.token_urlsafe(32)
        max_age = 43_200
        self.sessions[token] = {
            "user_id": user.id,
            "expires_at": time.time() + max_age,
        }
        secure = "; Secure" if os.environ.get("SALAMANDRA_COOKIE_SECURE") == "1" else ""
        self.send_json(
            {"state": self.state_payload(user)},
            headers={
                "Set-Cookie": (
                    f"salamandra_session={token}; Path=/; HttpOnly; SameSite=Lax; "
                    f"Max-Age={max_age}{secure}"
                )
            },
        )

    def handle_sign_out(self):
        token = self.session_token()
        if token:
            self.sessions.pop(token, None)
        self.send_json(
            {"state": self.state_payload(None)},
            headers={
                "Set-Cookie": (
                    "salamandra_session=; Path=/; HttpOnly; Max-Age=0; SameSite=Lax"
                )
            },
        )

    def create_item_from_body(self, body: dict) -> ItemNode:
        requirements = [
            Requirement(
                item_id=requirement.get("item_id", ""),
                amount=int(requirement.get("amount", 1)),
            )
            for requirement in body.get("requirements", [])
            if requirement.get("item_id")
        ]

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

    def plan_request_from_template(self, body: dict) -> dict:
        source_type = body.get("source_type", "template")
        source_id = body.get("source_id", "")
        source = (
            self.templates.get_kit(source_id)
            if source_type == "kit"
            else self.templates.get_template(source_id)
        )
        if source is None:
            raise ValueError("Template or kit was not found.")
        return {"items": [item.to_dict() for item in source.items]}

    def event_from_body(self, body: dict, user: UserAccount) -> EventRecord:
        event_id = self.required_identifier(body.get("event_id"), "event_id")
        event = self.memory.get_for_organization(event_id, user.organization_id)
        if event is None:
            raise ResourceNotFound("Event was not found.")
        return event

    def create_event_from_request(
        self,
        body: dict,
        user: UserAccount,
    ) -> EventRecord:
        client_event = body.get("event", {})
        if client_event is not None and not isinstance(client_event, dict):
            raise ValueError("Event data must be an object.")
        client_event = client_event or {}

        supplied_id = client_event.get("id")
        if supplied_id not in (None, ""):
            supplied_id = self.required_identifier(supplied_id, "event.id")
            existing = self.memory.get(supplied_id)
            if existing is not None and existing.organization_id != user.organization_id:
                raise ResourceNotFound("Event was not found.")
            if existing is not None:
                raise StateConflict(
                    "Existing events must be changed through an explicit event action."
                )

        description = body.get("description", client_event.get("description", ""))
        if not isinstance(description, str) or not description.strip():
            raise ValueError("Event description is required.")
        if len(description) > 20_000:
            raise ValueError("Event description is too long.")

        overrides = body.get("overrides")
        if overrides is None:
            overrides = {
                name: client_event[name]
                for name in (
                    "title",
                    "start_date",
                    "start_time",
                    "location",
                    "duration_minutes",
                )
                if name in client_event
            }
        if not isinstance(overrides, dict):
            raise ValueError("Event overrides must be an object.")
        allowed_overrides = {
            name: overrides[name]
            for name in (
                "title",
                "start_date",
                "start_time",
                "location",
                "duration_minutes",
            )
            if name in overrides
        }

        draft = EventDescriptionPlanner(
            self.workspace.combined_inventory(user.id, user.organization_id),
            self.catalog,
            self.memory,
            organization_id=user.organization_id,
            item_classes=self.item_classes,
        ).draft_from_description(description, overrides=allowed_overrides)
        event = draft.record
        event.organization_id = user.organization_id
        event.owner_id = user.id
        event.assigned_user_ids = [user.id]
        event.status = "planning"
        event.history = []
        event.movements = []
        return event

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
                },
                "organization": {},
                "presence": [],
                "inventory": inventories["combined"],
                "inventories": inventories,
                "presets": {"presets": [], "tags": []},
                "templates": {"templates": [], "kits": []},
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
        active_user_ids = {
            str(session.get("user_id", ""))
            for session in self.sessions.values()
            if float(session.get("expires_at", 0)) > time.time()
        }
        auth_payload = {
            "authenticated": user is not None,
            "user": user.to_public_dict() if user else None,
            "users": [account.to_public_dict() for account in organization_users],
            "demo_available": self.accounts.allow_demo,
        }
        inventories = self.workspace.to_dict(
            user.id if user else None,
            user.organization_id if user else None,
        )
        events = self.memory.to_dict(user.organization_id if user else None)
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
            "templates": self.templates.to_dict(),
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
            "plan": "Operations Pro" if user.organization_id == "northstar-live" else "Internal",
            "seat_count": len(users),
            "seat_limit": 12,
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
            affected = self.memory.get(update.get("event_id", ""))
            if affected is None or affected.organization_id != user.organization_id:
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
        if not reader.fieldnames or "id" not in reader.fieldnames:
            raise ValueError("Inventory CSV must include an id column.")

        scope = self.authorized_inventory_scope(body, user)
        inventory = self.workspace.inventory_for(scope, user.id, user.organization_id)
        staged: list[ItemNode] = []
        seen_ids: set[str] = set()
        for row_number, row in enumerate(reader, start=2):
            if row_number > 10_001:
                raise ValueError("Inventory CSV cannot contain more than 10,000 rows.")
            if any(len(str(value or "")) > 4_096 for value in row.values()):
                raise ValueError(f"CSV row {row_number} contains a field that is too long.")
            item_id = str(row.get("id", "")).strip()
            if not item_id:
                continue
            normalized_id = item_id.strip().lower()
            if normalized_id in seen_ids:
                raise ValueError(f"Inventory CSV contains duplicate item id {item_id}.")
            seen_ids.add(normalized_id)
            count = int(row.get("count") or 0)
            if count < 0:
                raise ValueError(f"Count cannot be negative for {item_id}.")
            existing = inventory.get_item(item_id)
            item = ItemNode(
                id=item_id,
                type=row.get("type") or (existing.type if existing else "other"),
                count=count,
                info=str(row.get("info", "")),
                class_id=str(row.get("class_id", "")),
                manufacturer=str(row.get("manufacturer", "")),
                model=str(row.get("model", "")),
                condition=str(row.get("condition", "ready")),
                capabilities=tuple(
                    value.strip()
                    for value in str(row.get("capabilities", "")).split(";")
                    if value.strip()
                ),
                connectors=tuple(
                    value.strip()
                    for value in str(row.get("connectors", "")).split(";")
                    if value.strip()
                ),
                quality_score=int(row.get("quality_score") or 0),
                preference_score=int(row.get("preference_score") or 0),
                weight_kg=float(row.get("weight_kg") or 0),
                in_use_count=existing.in_use_count if existing else 0,
            )
            staged.append(item)

        if not staged:
            raise ValueError("The CSV did not contain any inventory rows.")
        for item in staged:
            inventory.items[item.id] = item
        self.save_workspace()
        self.integrations.record_transfer(user.organization_id, "imported")
        return len(staged)

    def current_user(self) -> UserAccount | None:
        token = self.session_token()
        if not token:
            return None
        session = self.sessions.get(token)
        if not session:
            return None
        if float(session.get("expires_at", 0)) <= time.time():
            self.sessions.pop(token, None)
            return None
        user_id = str(session.get("user_id", ""))
        return self.accounts.get(user_id) if user_id else None

    def require_user(self) -> UserAccount:
        user = self.current_user()
        if user is None:
            raise AuthenticationRequired()
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

    def validate_request_origin(self):
        origin = self.headers.get("Origin", "").rstrip("/")
        if not origin:
            return
        configured = {
            value.strip().rstrip("/")
            for value in os.environ.get("SALAMANDRA_ALLOWED_ORIGINS", "").split(",")
            if value.strip()
        }
        host = self.headers.get("Host", "")
        allowed = configured or {f"http://{host}", f"https://{host}"}
        if origin not in allowed:
            raise AccessDenied("Cross-origin requests are not allowed.")

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
        if os.environ.get("SALAMANDRA_COOKIE_SECURE") == "1":
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")

    def log_message(self, format, *args):
        return


def run(host: str = "127.0.0.1", port: int = 8000):
    demo_enabled = os.environ.get("SALAMANDRA_ENABLE_DEMO") == "1"
    SalamandraServer.accounts = AccountStore(
        USERS_PATH,
        seed_defaults=demo_enabled,
        allow_demo=demo_enabled,
    )
    SalamandraServer.workspace = InventoryWorkspace(WORKSPACE_PATH, INVENTORY_PATH)
    SalamandraServer.workspace.migrate_preset_metadata(SalamandraServer.catalog)
    SalamandraServer.memory = EventMemory(EVENTS_PATH)
    SalamandraServer.item_classes = ItemClassCatalog(ITEM_CLASSES_PATH)
    SalamandraServer.integrations = IntegrationStore(INTEGRATIONS_PATH)
    SalamandraServer.sessions = {}
    SalamandraServer.login_attempts = {}
    server = ThreadingHTTPServer((host, port), SalamandraServer)
    print(f"Salamandra running at http://{host}:{port}")
    print("Press Ctrl+C to stop.")
    server.serve_forever()


if __name__ == "__main__":
    run()
