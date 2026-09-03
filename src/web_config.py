from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlsplit


PRODUCTION_LIKE_MODES = frozenset({"staging", "production"})
VALID_MODES = frozenset({"development", "test", *PRODUCTION_LIKE_MODES})
VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
VALID_REGISTRATION_MODES = frozenset({"disabled", "invite_only", "open"})


class ConfigurationError(RuntimeError):
    pass


def _enabled(environment: Mapping[str, str], name: str, default: bool = False) -> bool:
    raw = environment.get(name)
    if raw is None or not str(raw).strip():
        return default
    normalized = str(raw).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be a boolean value.")


def _csv(environment: Mapping[str, str], name: str) -> tuple[str, ...]:
    return tuple(
        value.strip()
        for value in str(environment.get(name, "")).split(",")
        if value.strip()
    )


def normalize_origin(value: str, variable_name: str) -> str:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ConfigurationError(
            f"{variable_name} must contain an HTTP(S) origin without credentials or a path."
        )
    try:
        port = parsed.port
    except ValueError as error:
        raise ConfigurationError(f"{variable_name} contains an invalid port.") from error
    host = parsed.hostname.lower().rstrip(".")
    default_port = 443 if parsed.scheme == "https" else 80
    authority = host if port in {None, default_port} else f"{host}:{port}"
    return f"{parsed.scheme}://{authority}"


def normalize_host(value: str, variable_name: str = "SALAMANDRA_TRUSTED_HOSTS") -> str:
    raw = value.strip()
    if not raw or "/" in raw or "@" in raw or "*" in raw:
        raise ConfigurationError(f"{variable_name} contains an invalid host.")
    parsed = urlsplit(f"//{raw}")
    if not parsed.hostname:
        raise ConfigurationError(f"{variable_name} contains an invalid host.")
    try:
        parsed.port
    except ValueError as error:
        raise ConfigurationError(f"{variable_name} contains an invalid port.") from error
    return parsed.hostname.lower().rstrip(".")


def _proxy_addresses(values: tuple[str, ...]) -> frozenset[str]:
    addresses: set[str] = set()
    for value in values:
        try:
            addresses.add(str(ipaddress.ip_address(value)))
        except ValueError as error:
            raise ConfigurationError(
                "SALAMANDRA_TRUSTED_PROXY_IPS must contain IP addresses only."
            ) from error
    return frozenset(addresses)


@dataclass(frozen=True)
class WebConfig:
    mode: str
    database_url: str
    canonical_origin: str
    allowed_origins: frozenset[str]
    trusted_hosts: frozenset[str]
    secure_cookie: bool
    trust_proxy_headers: bool
    trusted_proxy_ips: frozenset[str]
    session_max_age_seconds: int
    log_level: str
    demo_enabled: bool
    json_dev_enabled: bool
    registration_mode: str

    @property
    def production_like(self) -> bool:
        return self.mode in PRODUCTION_LIKE_MODES

    @classmethod
    def local_default(cls) -> "WebConfig":
        return cls(
            mode="test",
            database_url="",
            canonical_origin="http://127.0.0.1:8000",
            allowed_origins=frozenset(
                {
                    "http://127.0.0.1:8000",
                    "http://localhost:8000",
                    "http://127.0.0.1:5173",
                    "http://localhost:5173",
                }
            ),
            trusted_hosts=frozenset({"127.0.0.1", "localhost", "::1"}),
            secure_cookie=False,
            trust_proxy_headers=False,
            trusted_proxy_ips=frozenset(),
            session_max_age_seconds=43_200,
            log_level="WARNING",
            demo_enabled=False,
            json_dev_enabled=True,
            registration_mode="disabled",
        )

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "WebConfig":
        values = os.environ if environment is None else environment
        mode = str(values.get("SALAMANDRA_ENV", "development")).strip().lower()
        if mode not in VALID_MODES:
            raise ConfigurationError(
                "SALAMANDRA_ENV must be development, test, staging, or production."
            )

        production_like = mode in PRODUCTION_LIKE_MODES
        raw_origin = str(values.get("SALAMANDRA_APP_ORIGIN", "")).strip()
        if not raw_origin:
            if production_like:
                raise ConfigurationError(
                    "SALAMANDRA_APP_ORIGIN is required in staging and production."
                )
            raw_origin = "http://127.0.0.1:8000"
        canonical_origin = normalize_origin(raw_origin, "SALAMANDRA_APP_ORIGIN")

        configured_origins = _csv(values, "SALAMANDRA_ALLOWED_ORIGINS")
        if production_like and not configured_origins:
            raise ConfigurationError(
                "SALAMANDRA_ALLOWED_ORIGINS is required in staging and production."
            )
        origin_values = configured_origins or (
            (canonical_origin,)
            if production_like
            else (
                canonical_origin,
                "http://127.0.0.1:8000",
                "http://localhost:8000",
                "http://127.0.0.1:5173",
                "http://localhost:5173",
            )
        )
        allowed_origins = frozenset(
            normalize_origin(origin, "SALAMANDRA_ALLOWED_ORIGINS")
            for origin in origin_values
        )

        configured_hosts = _csv(values, "SALAMANDRA_TRUSTED_HOSTS")
        if production_like and not configured_hosts:
            raise ConfigurationError(
                "SALAMANDRA_TRUSTED_HOSTS is required in staging and production."
            )
        trusted_hosts = frozenset(
            normalize_host(host)
            for host in (
                configured_hosts
                or (urlsplit(canonical_origin).hostname or "127.0.0.1", "localhost")
            )
        )

        secure_cookie = _enabled(values, "SALAMANDRA_COOKIE_SECURE")
        trust_proxy_headers = _enabled(values, "SALAMANDRA_TRUST_PROXY_HEADERS")
        trusted_proxy_ips = _proxy_addresses(_csv(values, "SALAMANDRA_TRUSTED_PROXY_IPS"))
        demo_enabled = _enabled(values, "SALAMANDRA_ENABLE_DEMO")
        json_dev_enabled = _enabled(values, "SALAMANDRA_ALLOW_JSON_DEV")
        database_url = str(values.get("SALAMANDRA_DATABASE_URL", "")).strip()
        registration_mode = str(
            values.get("SALAMANDRA_REGISTRATION_MODE", "disabled")
        ).strip().lower()
        if registration_mode not in VALID_REGISTRATION_MODES:
            raise ConfigurationError(
                "SALAMANDRA_REGISTRATION_MODE must be disabled, invite_only, or open."
            )

        try:
            session_max_age = int(values.get("SALAMANDRA_SESSION_MAX_AGE_SECONDS", "43200"))
        except ValueError as error:
            raise ConfigurationError(
                "SALAMANDRA_SESSION_MAX_AGE_SECONDS must be an integer."
            ) from error
        if session_max_age < 300 or session_max_age > 604_800:
            raise ConfigurationError(
                "SALAMANDRA_SESSION_MAX_AGE_SECONDS must be between 300 and 604800."
            )
        log_level = str(values.get("SALAMANDRA_LOG_LEVEL", "INFO")).strip().upper()
        if log_level not in VALID_LOG_LEVELS:
            raise ConfigurationError("SALAMANDRA_LOG_LEVEL is invalid.")

        if production_like:
            if not database_url:
                raise ConfigurationError(
                    "SALAMANDRA_DATABASE_URL is required in staging and production."
                )
            if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
                raise ConfigurationError(
                    "SALAMANDRA_DATABASE_URL must use PostgreSQL in staging and production."
                )
            if canonical_origin not in allowed_origins:
                raise ConfigurationError(
                    "SALAMANDRA_ALLOWED_ORIGINS must include SALAMANDRA_APP_ORIGIN."
                )
            canonical_host = urlsplit(canonical_origin).hostname or ""
            if canonical_host not in trusted_hosts:
                raise ConfigurationError(
                    "SALAMANDRA_TRUSTED_HOSTS must include the canonical application host."
                )
            if urlsplit(canonical_origin).scheme != "https":
                raise ConfigurationError(
                    "SALAMANDRA_APP_ORIGIN must use HTTPS in staging and production."
                )
            if any(urlsplit(origin).scheme != "https" for origin in allowed_origins):
                raise ConfigurationError(
                    "All staging and production origins must use HTTPS."
                )
            if not secure_cookie:
                raise ConfigurationError(
                    "SALAMANDRA_COOKIE_SECURE must be enabled in staging and production."
                )
            if demo_enabled or json_dev_enabled:
                raise ConfigurationError(
                    "Demo and JSON development modes cannot be enabled in staging or production."
                )
            if canonical_host in {"localhost", "127.0.0.1", "::1"}:
                raise ConfigurationError(
                    "Staging and production require a non-local canonical application host."
                )

        if trust_proxy_headers and not trusted_proxy_ips:
            raise ConfigurationError(
                "SALAMANDRA_TRUSTED_PROXY_IPS is required when proxy headers are trusted."
            )

        return cls(
            mode=mode,
            database_url=database_url,
            canonical_origin=canonical_origin,
            allowed_origins=allowed_origins,
            trusted_hosts=trusted_hosts,
            secure_cookie=secure_cookie,
            trust_proxy_headers=trust_proxy_headers,
            trusted_proxy_ips=trusted_proxy_ips,
            session_max_age_seconds=session_max_age,
            log_level=log_level,
            demo_enabled=demo_enabled,
            json_dev_enabled=json_dev_enabled,
            registration_mode=registration_mode,
        )
