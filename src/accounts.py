from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError


HASH_ITERATIONS = 120_000
PASSWORD_HASHER = PasswordHasher()
LEGACY_DEMO_USER_IDS = frozenset(
    {"admin", "ops", "demo-owner", "demo-tech", "demo-producer"}
)


def default_preferences() -> dict[str, Any]:
    return {
        "theme": "system",
        "font_scale": "comfortable",
        "density": "comfortable",
        "show_progress": True,
        "onboarding_dismissed": False,
    }


@dataclass
class UserAccount:
    name: str
    email: str
    role: str = "operator"
    organization_id: str = "salamandra"
    organization_name: str = "Salamandra Event Operations"
    title: str = "Event Operations"
    warehouse: str = "Main Warehouse"
    avatar_url: str = ""
    id: str = field(default_factory=lambda: uuid4().hex)
    password_hash: str = ""
    preferences: dict[str, Any] = field(default_factory=default_preferences)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "role": self.role,
            "organization_id": self.organization_id,
            "organization_name": self.organization_name,
            "title": self.title,
            "warehouse": self.warehouse,
            "avatar_url": self.avatar_url,
            "preferences": self.preferences,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.to_public_dict(),
            "password_hash": self.password_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserAccount:
        return cls(
            id=data.get("id", uuid4().hex),
            name=data.get("name", ""),
            email=data.get("email", "").strip().lower(),
            role=data.get("role", "operator"),
            organization_id=data.get("organization_id", "salamandra"),
            organization_name=data.get(
                "organization_name",
                "Salamandra Event Operations",
            ),
            title=data.get("title", "Event Operations"),
            warehouse=data.get("warehouse", "Main Warehouse"),
            avatar_url=data.get("avatar_url", ""),
            password_hash=data.get("password_hash", ""),
            preferences={
                **default_preferences(),
                **dict(data.get("preferences", {})),
            },
        )


class AccountStore:
    def __init__(
        self,
        path: str | Path,
        *,
        seed_defaults: bool = False,
        allow_demo: bool = False,
    ):
        self.path = Path(path)
        self.allow_demo = allow_demo
        self.users = self._load()
        if seed_defaults and self._ensure_default_users():
            self.save()

    def authenticate(self, email: str, password: str) -> UserAccount | None:
        normalized_email = email.strip().lower()
        for user in self.users.values():
            if user.email != normalized_email:
                continue
            if user.id in LEGACY_DEMO_USER_IDS:
                return None
            if not self.verify_password(password, user.password_hash):
                return None
            if not user.password_hash.startswith("$argon2"):
                user.password_hash = self.hash_password(password)
                self.save()
            return user
        return None

    def get(self, user_id: str) -> UserAccount | None:
        return self.users.get(user_id)

    def list_users(self) -> list[UserAccount]:
        return sorted(self.users.values(), key=lambda user: user.name)

    def list_organization_users(self, organization_id: str) -> list[UserAccount]:
        return [
            user
            for user in self.list_users()
            if user.organization_id == organization_id
        ]

    def create_user(
        self,
        name: str,
        email: str,
        password: str,
        role: str,
        organization_id: str,
        organization_name: str,
        title: str = "Event Operations",
        warehouse: str = "Main Warehouse",
    ) -> UserAccount:
        normalized_email = email.strip().lower()
        if not name.strip():
            raise ValueError("Team member name is required.")
        if "@" not in normalized_email:
            raise ValueError("A valid team member email is required.")
        if len(password) < 6:
            raise ValueError("Temporary password must be at least 6 characters.")
        if any(user.email == normalized_email for user in self.users.values()):
            raise ValueError("An account with this email already exists.")

        user = UserAccount(
            name=name.strip(),
            email=normalized_email,
            role=role.strip().lower() or "operator",
            organization_id=organization_id,
            organization_name=organization_name,
            title=title.strip() or "Event Operations",
            warehouse=warehouse.strip() or "Main Warehouse",
            password_hash=self.hash_password(password),
        )
        self.users[user.id] = user
        self.save()
        return user

    def update_profile(
        self,
        user_id: str,
        *,
        name: str,
        title: str,
        warehouse: str,
    ) -> UserAccount:
        user = self.get(user_id)
        if user is None:
            raise ValueError("Account was not found.")
        if not name.strip():
            raise ValueError("Name is required.")
        user.name = name.strip()
        user.title = title.strip() or user.title
        user.warehouse = warehouse.strip() or user.warehouse
        self.save()
        return user

    def update_preferences(self, user_id: str, **preferences: Any) -> UserAccount:
        user = self.get(user_id)
        if user is None:
            raise ValueError("Account was not found.")

        allowed = {
            "theme": {"system", "light", "dark"},
            "font_scale": {"compact", "comfortable", "large", "largest"},
            "density": {"compact", "comfortable"},
        }
        updated = {**default_preferences(), **user.preferences}
        for name, choices in allowed.items():
            if name not in preferences:
                continue
            value = str(preferences[name]).strip().lower()
            if value not in choices:
                raise ValueError(f"Invalid {name.replace('_', ' ')} preference.")
            updated[name] = value
        if "show_progress" in preferences:
            updated["show_progress"] = bool(preferences["show_progress"])
        if "onboarding_dismissed" in preferences:
            updated["onboarding_dismissed"] = bool(
                preferences["onboarding_dismissed"]
            )
        user.preferences = updated
        self.save()
        return user

    def to_dict(self) -> dict[str, Any]:
        return {"users": [user.to_dict() for user in self.list_users()]}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as file:
            json.dump(self.to_dict(), file, indent=4)

    @staticmethod
    def hash_password(password: str, salt: str | None = None) -> str:
        if salt is None:
            return PASSWORD_HASHER.hash(password)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            HASH_ITERATIONS,
        ).hex()
        return f"pbkdf2_sha256${HASH_ITERATIONS}${salt}${digest}"

    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        if password_hash.startswith("$argon2"):
            try:
                return PASSWORD_HASHER.verify(password_hash, password)
            except (InvalidHashError, VerifyMismatchError):
                return False
        try:
            scheme, iterations, salt, expected_digest = password_hash.split("$", 3)
        except ValueError:
            return False
        if scheme != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            int(iterations),
        ).hex()
        return secrets.compare_digest(digest, expected_digest)

    def _load(self) -> dict[str, UserAccount]:
        if self.path.exists():
            with open(self.path, "r", encoding="utf-8") as file:
                data = json.load(file)
            return {
                user.id: user
                for user in (
                    UserAccount.from_dict(user_data)
                    for user_data in data.get("users", [])
                )
            }

        return {}

    def _ensure_default_users(self) -> bool:
        changed = False
        existing_emails = {user.email for user in self.users.values()}
        for default_user in self._default_users():
            if default_user.email in existing_emails:
                continue
            self.users[default_user.id] = default_user
            existing_emails.add(default_user.email)
            changed = True
        return changed

    def _default_users(self) -> list[UserAccount]:
        generated_password = secrets.token_urlsafe(32)
        return [
            UserAccount(
                id="admin",
                name="Warehouse Admin",
                email="admin@salamandra.local",
                role="admin",
                title="Warehouse Administrator",
                password_hash=self.hash_password(generated_password),
            ),
            UserAccount(
                id="ops",
                name="Ops User",
                email="ops@salamandra.local",
                role="operator",
                title="Event Technician",
                password_hash=self.hash_password(generated_password),
            ),
            UserAccount(
                id="demo-owner",
                name="Maya Cohen",
                email="maya@northstarlive.demo",
                role="owner",
                organization_id="northstar-live",
                organization_name="Northstar Live",
                title="Head of Event Operations",
                warehouse="Tel Aviv HQ",
                avatar_url="/assets/demo-operator.png",
                password_hash=self.hash_password(generated_password),
            ),
            UserAccount(
                id="demo-tech",
                name="Eli Barak",
                email="eli@northstarlive.demo",
                role="technician",
                organization_id="northstar-live",
                organization_name="Northstar Live",
                title="Audio Lead",
                warehouse="Tel Aviv HQ",
                password_hash=self.hash_password(generated_password),
            ),
            UserAccount(
                id="demo-producer",
                name="Noa Levi",
                email="noa@northstarlive.demo",
                role="producer",
                organization_id="northstar-live",
                organization_name="Northstar Live",
                title="Event Producer",
                warehouse="Tel Aviv HQ",
                password_hash=self.hash_password(generated_password),
            ),
        ]
