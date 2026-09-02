from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


SENSITIVE_FIELD_NAMES = frozenset(
    {
        "password",
        "token",
        "cookie",
        "authorization",
        "database_url",
        "secret",
        "api_key",
        "access_token",
        "refresh_token",
    }
)


def _safe_value(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    cleaned = " ".join(str(value).split())
    return cleaned[:256]


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event_name", "application_log"),
        }
        context = getattr(record, "event_context", {})
        for key, value in context.items():
            if key.lower() in SENSITIVE_FIELD_NAMES:
                continue
            payload[key] = _safe_value(value)
        if record.exc_info:
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def configure_logging(level: str) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    root.addHandler(handler)
    root.setLevel(level)


def log_event(
    logger: logging.Logger,
    level: int,
    event_name: str,
    **context: Any,
) -> None:
    safe_context = {
        key: value
        for key, value in context.items()
        if key.lower() not in SENSITIVE_FIELD_NAMES
    }
    logger.log(
        level,
        event_name,
        extra={"event_name": event_name, "event_context": safe_context},
    )
