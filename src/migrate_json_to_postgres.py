from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from database import (
    EventModel,
    IntegrationConnectionModel,
    InventoryHoldingModel,
    ItemClassRecordModel,
    MembershipModel,
    OrganizationModel,
    UserModel,
    create_database_engine,
    session_factory,
)


@dataclass
class MigrationReport:
    organizations: int = 0
    users: int = 0
    memberships: int = 0
    holdings: int = 0
    events: int = 0
    item_classes: int = 0
    integrations: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "organizations": self.organizations,
            "users": self.users,
            "memberships": self.memberships,
            "holdings": self.holdings,
            "events": self.events,
            "item_classes": self.item_classes,
            "integrations": self.integrations,
            "warnings": self.warnings,
        }


def load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must contain a JSON object.")
    return data


def migrate_json(
    factory: sessionmaker[Session],
    docs_dir: Path,
    *,
    dry_run: bool = False,
) -> MigrationReport:
    users_data = load_json(docs_dir / "users.json", {"users": []})
    inventories_data = load_json(
        docs_dir / "inventories.json",
        {"shared": {}, "personal": {}},
    )
    events_data = load_json(docs_dir / "events.json", {"events": []})
    item_classes_data = load_json(
        docs_dir / "item_classes.json",
        {"organizations": {}},
    )
    integrations_data = load_json(
        docs_dir / "integrations.json",
        {"organizations": {}},
    )
    report = MigrationReport()

    raw_users = list(users_data.get("users", []))
    users_by_id = {str(user.get("id", "")): user for user in raw_users if user.get("id")}
    organization_names: dict[str, str] = {}
    for user in raw_users:
        organization_id = str(user.get("organization_id", "")).strip()
        if organization_id:
            organization_names[organization_id] = str(
                user.get("organization_name") or organization_id
            )
    organization_names.update(
        {
            str(organization_id): str(organization_id)
            for organization_id in inventories_data.get("shared", {})
        }
    )
    for source in (item_classes_data, integrations_data):
        organization_names.update(
            {
                str(organization_id): str(organization_id)
                for organization_id in source.get("organizations", {})
            }
        )
    organization_names.update(
        {
            str(event.get("organization_id", "salamandra")): str(
                event.get("organization_id", "salamandra")
            )
            for event in events_data.get("events", [])
        }
    )

    with factory() as session:
        transaction = session.begin()
        try:
            for organization_id, name in sorted(organization_names.items()):
                if not session.get(OrganizationModel, organization_id):
                    session.add(OrganizationModel(id=organization_id, name=name))
                    report.organizations += 1
            session.flush()

            for raw_user in raw_users:
                user_id = str(raw_user.get("id", "")).strip()
                email = str(raw_user.get("email", "")).strip().lower()
                organization_id = str(raw_user.get("organization_id", "")).strip()
                if not user_id or not email or not organization_id:
                    report.warnings.append("Skipped a user missing id, email, or organization.")
                    continue
                if not session.get(UserModel, user_id):
                    session.add(
                        UserModel(
                            id=user_id,
                            email=email,
                            name=str(raw_user.get("name", "Unnamed user")),
                            password_hash=str(raw_user.get("password_hash", "")),
                            preferences=dict(raw_user.get("preferences", {})),
                        )
                    )
                    report.users += 1
                membership = session.scalar(
                    select(MembershipModel).where(
                        MembershipModel.organization_id == organization_id,
                        MembershipModel.user_id == user_id,
                    )
                )
                if membership is None:
                    session.add(
                        MembershipModel(
                            organization_id=organization_id,
                            user_id=user_id,
                            role=str(raw_user.get("role", "read_only")),
                        )
                    )
                    report.memberships += 1
            session.flush()

            for organization_id, inventory in inventories_data.get("shared", {}).items():
                report.holdings += _migrate_inventory(
                    session,
                    str(organization_id),
                    "shared",
                    None,
                    inventory,
                    report,
                )
            for user_id, inventory in inventories_data.get("personal", {}).items():
                raw_user = users_by_id.get(str(user_id))
                if raw_user is None:
                    report.warnings.append(
                        f"Skipped personal inventory for unknown user {user_id}."
                    )
                    continue
                report.holdings += _migrate_inventory(
                    session,
                    str(raw_user.get("organization_id", "")),
                    "personal",
                    str(user_id),
                    inventory,
                    report,
                )

            for raw_event in events_data.get("events", []):
                event_id = str(raw_event.get("id", "")).strip()
                organization_id = str(raw_event.get("organization_id", "")).strip()
                owner_id = str(raw_event.get("owner_id", "")).strip()
                if not event_id or not organization_id or owner_id not in users_by_id:
                    report.warnings.append(
                        f"Skipped event {event_id or '<missing id>'} with invalid ownership."
                    )
                    continue
                if session.get(EventModel, event_id):
                    continue
                starts_at, ends_at = _event_window(raw_event, report)
                migrated_event = dict(raw_event)
                migrated_event["plan_verified"] = False
                report.warnings.append(
                    f"Event {event_id} requires a server-side plan regeneration before reservation."
                )
                session.add(
                    EventModel(
                        id=event_id,
                        organization_id=organization_id,
                        owner_user_id=owner_id,
                        title=str(raw_event.get("title", "Untitled event")),
                        status=str(raw_event.get("status", "planning")),
                        starts_at=starts_at,
                        ends_at=ends_at,
                        priority_score=int(raw_event.get("priority_score", 50)),
                        data=migrated_event,
                    )
                )
                report.events += 1

            for organization_id, classes in item_classes_data.get(
                "organizations", {}
            ).items():
                for class_id, values in classes.items():
                    existing = session.scalar(
                        select(ItemClassRecordModel).where(
                            ItemClassRecordModel.organization_id == organization_id,
                            ItemClassRecordModel.class_id == class_id,
                        )
                    )
                    if existing is None:
                        session.add(
                            ItemClassRecordModel(
                                organization_id=str(organization_id),
                                class_id=str(class_id),
                                data=dict(values),
                            )
                        )
                        report.item_classes += 1

            for organization_id, integrations in integrations_data.get(
                "organizations", {}
            ).items():
                for integration_id, values in integrations.items():
                    existing = session.scalar(
                        select(IntegrationConnectionModel).where(
                            IntegrationConnectionModel.organization_id == organization_id,
                            IntegrationConnectionModel.integration_id == integration_id,
                        )
                    )
                    if existing is None:
                        safe_values = {
                            key: value
                            for key, value in dict(values).items()
                            if key not in {"api_key", "access_token", "refresh_token", "secret"}
                        }
                        session.add(
                            IntegrationConnectionModel(
                                organization_id=str(organization_id),
                                integration_id=str(integration_id),
                                data=safe_values,
                            )
                        )
                        report.integrations += 1

            session.flush()
            if dry_run:
                transaction.rollback()
            else:
                transaction.commit()
        except Exception:
            transaction.rollback()
            raise
    return report


def _migrate_inventory(
    session: Session,
    organization_id: str,
    scope: str,
    owner_user_id: str | None,
    inventory: dict[str, Any],
    report: MigrationReport,
) -> int:
    migrated = 0
    for raw_item in inventory.get("items", []):
        item_id = str(raw_item.get("id", "")).strip().lower()
        if not item_id:
            report.warnings.append("Skipped an inventory row without an id.")
            continue
        existing = session.scalar(
            select(InventoryHoldingModel).where(
                InventoryHoldingModel.organization_id == organization_id,
                InventoryHoldingModel.scope == scope,
                InventoryHoldingModel.owner_user_id == owner_user_id,
                InventoryHoldingModel.legacy_item_id == item_id,
            )
        )
        if existing is not None:
            continue
        in_use = int(raw_item.get("in_use_count", 0))
        if in_use:
            report.warnings.append(
                f"Imported {in_use} unattributed dispatched units for {organization_id}/{item_id}."
            )
        session.add(
            InventoryHoldingModel(
                organization_id=organization_id,
                legacy_item_id=item_id,
                scope=scope,
                owner_user_id=owner_user_id,
                available_quantity=int(raw_item.get("count", 0)),
                dispatched_quantity=in_use,
                data=dict(raw_item),
            )
        )
        migrated += 1
    return migrated


def _event_window(
    raw_event: dict[str, Any],
    report: MigrationReport,
) -> tuple[datetime | None, datetime | None]:
    try:
        starts_at = datetime.fromisoformat(
            f"{raw_event.get('start_date')}T{raw_event.get('start_time', '10:00')}:00"
        )
        duration = int(raw_event.get("duration_minutes", 240))
        from datetime import timedelta

        return starts_at, starts_at + timedelta(minutes=duration)
    except (TypeError, ValueError):
        report.warnings.append(
            f"Event {raw_event.get('id', '<unknown>')} has an invalid time window."
        )
        return None, None


def main():
    parser = argparse.ArgumentParser(description="Migrate Salamandra JSON data to PostgreSQL.")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--docs-dir", type=Path, default=Path("docs"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    engine = create_database_engine(args.database_url, production=True)
    report = migrate_json(session_factory(engine), args.docs_dir, dry_run=args.dry_run)
    print(json.dumps(report.to_dict(), indent=2))


if __name__ == "__main__":
    main()
