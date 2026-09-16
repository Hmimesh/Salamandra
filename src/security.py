from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Permission(StrEnum):
    STATE_READ = "state.read"
    INVENTORY_READ = "inventory.read"
    INVENTORY_PERSONAL_WRITE = "inventory.personal.write"
    INVENTORY_SHARED_WRITE = "inventory.shared.write"
    INVENTORY_DEFINITION_MANAGE = "inventory.definition.manage"
    INVENTORY_EXPORT = "inventory.export"
    INVENTORY_IMPORT = "inventory.import"
    INVENTORY_CLEAR = "inventory.clear"
    MAINTENANCE_READ = "maintenance.read"
    MAINTENANCE_REPORT = "maintenance.report"
    MAINTENANCE_MANAGE = "maintenance.manage"
    ITEM_CLASSES_MANAGE = "item_classes.manage"
    CATALOG_MANAGE = "catalog.manage"
    EVENTS_PLAN = "events.plan"
    EVENTS_CREATE = "events.create"
    EVENTS_UPDATE = "events.update"
    EVENTS_CANCEL = "events.cancel"
    EVENTS_DELETE = "events.delete"
    KITS_MANAGE = "kits.manage"
    OPERATIONS_PACK = "operations.pack"
    OPERATIONS_DISPATCH = "operations.dispatch"
    OPERATIONS_RETURN = "operations.return"
    EVENTS_ADJUST = "events.adjust"
    LOGISTICS_EDIT = "logistics.edit"
    CREW_READ = "crew.read"
    CREW_MANAGE = "crew.manage"
    CREW_ASSIGN = "crew.assign"
    CREW_OVERRIDE = "crew.override"
    CREW_ACCESS = "crew.access"
    INTEGRATIONS_MANAGE = "integrations.manage"
    MEMBERS_INVITE = "members.invite"
    SYNC_RUN = "sync.run"
    EVENTS_FEEDBACK_READ = "events.feedback.read"
    EVENTS_FEEDBACK_WRITE = "events.feedback.write"
    EVENTS_LEARNING_MANAGE = "events.learning.manage"
    LEARNING_OUTCOME_WRITE = "learning.outcome.write"
    LEARNING_SUMMARY_READ = "learning.summary.read"
    LEARNING_HISTORY_READ = "learning.history.read"


ALL_PERMISSIONS = frozenset(Permission)

ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    "owner": ALL_PERMISSIONS,
    "admin": ALL_PERMISSIONS - {Permission.INVENTORY_CLEAR},
    "producer": frozenset(
        {
            Permission.CREW_READ,
            Permission.CREW_ASSIGN,
            Permission.STATE_READ,
            Permission.INVENTORY_READ,
            Permission.INVENTORY_PERSONAL_WRITE,
            Permission.EVENTS_PLAN,
            Permission.EVENTS_ADJUST,
            Permission.LOGISTICS_EDIT,
            Permission.LEARNING_OUTCOME_WRITE,
            Permission.EVENTS_CREATE,
            Permission.EVENTS_UPDATE,
            Permission.EVENTS_CANCEL,
            Permission.KITS_MANAGE,
            Permission.OPERATIONS_PACK,
            Permission.SYNC_RUN,
            Permission.EVENTS_FEEDBACK_READ,
            Permission.EVENTS_FEEDBACK_WRITE,
        }
    ),
    "operator": frozenset(
        {
            Permission.CREW_READ,
            Permission.CREW_MANAGE,
            Permission.CREW_ASSIGN,
            Permission.CREW_OVERRIDE,
            Permission.CREW_ACCESS,
            Permission.MAINTENANCE_READ,
            Permission.EVENTS_ADJUST,
            Permission.LOGISTICS_EDIT,
            Permission.MAINTENANCE_REPORT,
            Permission.MAINTENANCE_MANAGE,
            Permission.STATE_READ,
            Permission.INVENTORY_READ,
            Permission.INVENTORY_PERSONAL_WRITE,
            Permission.INVENTORY_SHARED_WRITE,
            Permission.INVENTORY_DEFINITION_MANAGE,
            Permission.INVENTORY_EXPORT,
            Permission.INVENTORY_IMPORT,
            Permission.EVENTS_PLAN,
            Permission.LEARNING_OUTCOME_WRITE,
            Permission.EVENTS_CREATE,
            Permission.EVENTS_UPDATE,
            Permission.EVENTS_CANCEL,
            Permission.KITS_MANAGE,
            Permission.OPERATIONS_PACK,
            Permission.OPERATIONS_DISPATCH,
            Permission.OPERATIONS_RETURN,
            Permission.SYNC_RUN,
            Permission.EVENTS_FEEDBACK_READ,
            Permission.EVENTS_FEEDBACK_WRITE,
        }
    ),
    "technician": frozenset(
        {
            Permission.MAINTENANCE_REPORT,
            Permission.STATE_READ,
            Permission.EVENTS_ADJUST,
            Permission.INVENTORY_READ,
            Permission.INVENTORY_PERSONAL_WRITE,
            Permission.OPERATIONS_PACK,
            Permission.OPERATIONS_RETURN,
            Permission.EVENTS_FEEDBACK_READ,
            Permission.EVENTS_FEEDBACK_WRITE,
        }
    ),
    "freelancer": frozenset(
        {
            Permission.STATE_READ,
            Permission.INVENTORY_READ,
            Permission.INVENTORY_PERSONAL_WRITE,
            Permission.OPERATIONS_PACK,
            Permission.EVENTS_FEEDBACK_READ,
            Permission.EVENTS_FEEDBACK_WRITE,
        }
    ),
    "client": frozenset({Permission.STATE_READ, Permission.INVENTORY_READ}),
    "read_only": frozenset({Permission.STATE_READ, Permission.INVENTORY_READ}),
}


@dataclass
class ApiError(Exception):
    message: str
    status: int

    def __str__(self) -> str:
        return self.message


class AuthenticationRequired(ApiError):
    def __init__(self, message: str = "Sign in before accessing Salamandra data."):
        super().__init__(message, 401)


class AccessDenied(ApiError):
    def __init__(self, message: str = "Your account cannot perform this action."):
        super().__init__(message, 403)


class ResourceNotFound(ApiError):
    def __init__(self, message: str = "Resource was not found."):
        super().__init__(message, 404)


class StateConflict(ApiError):
    def __init__(self, message: str):
        super().__init__(message, 409)


def permissions_for_role(role: str) -> frozenset[Permission]:
    return ROLE_PERMISSIONS.get(role.strip().lower(), frozenset())


def require_permission(role: str, permission: Permission):
    if permission not in permissions_for_role(role):
        raise AccessDenied()


def validate_assignable_role(actor_role: str, requested_role: str) -> str:
    normalized = requested_role.strip().lower()
    if normalized not in ROLE_PERMISSIONS:
        raise ValueError("Team member role is not supported.")
    if actor_role != "owner" and normalized in {"owner", "admin"}:
        raise AccessDenied("Only an organization owner can grant administrator access.")
    return normalized
