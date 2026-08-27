from __future__ import annotations

import json
import os
from ipaddress import ip_address
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


DEFAULT_INTEGRATIONS: dict[str, dict[str, Any]] = {
    "crm": {
        "id": "crm",
        "name": "CRM",
        "provider": "generic",
        "status": "not_configured",
        "endpoint": "",
        "credential_env": "SALAMANDRA_CRM_API_KEY",
        "last_sync": "",
        "note": "Map customers and jobs to event records through a CRM API.",
    },
    "google_sheets": {
        "id": "google_sheets",
        "name": "Google Sheets",
        "provider": "google",
        "status": "not_configured",
        "spreadsheet_id": "",
        "sheet_name": "Inventory",
        "credential_env": "GOOGLE_APPLICATION_CREDENTIALS",
        "last_sync": "",
        "note": "Prepare organization inventory for a connected Google Sheet.",
    },
    "excel": {
        "id": "excel",
        "name": "Excel / CSV",
        "provider": "csv",
        "status": "ready",
        "last_sync": "",
        "note": "Import and export inventory with an Excel-compatible CSV file.",
    },
}


class IntegrationStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.organizations = self._load()

    def get_all(self, organization_id: str) -> dict[str, dict[str, Any]]:
        configured = self.organizations.get(organization_id, {})
        result = deepcopy(DEFAULT_INTEGRATIONS)
        for integration_id, values in configured.items():
            if integration_id in result:
                result[integration_id].update(values)
        for integration in result.values():
            credential_env = integration.get("credential_env", "")
            integration["credential_present"] = bool(
                credential_env and os.environ.get(credential_env)
            )
        return result

    def configure(
        self,
        organization_id: str,
        integration_id: str,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        if integration_id not in DEFAULT_INTEGRATIONS:
            raise ValueError("Integration was not found.")
        if integration_id == "excel":
            raise ValueError("Excel / CSV is available without configuration.")

        allowed_fields = {
            "crm": {"provider", "endpoint"},
            "google_sheets": {"spreadsheet_id", "sheet_name"},
        }[integration_id]
        cleaned = {
            name: str(values.get(name, "")).strip()
            for name in allowed_fields
        }
        if integration_id == "crm" and not cleaned["endpoint"]:
            raise ValueError("CRM API endpoint is required.")
        if integration_id == "google_sheets" and not cleaned["spreadsheet_id"]:
            raise ValueError("Google spreadsheet ID is required.")
        if integration_id == "crm":
            if cleaned["provider"] not in {"generic", "hubspot", "salesforce"}:
                raise ValueError("CRM provider is not supported.")
            self._validate_https_endpoint(cleaned["endpoint"])
        if integration_id == "google_sheets":
            if len(cleaned["spreadsheet_id"]) > 256 or len(cleaned["sheet_name"]) > 128:
                raise ValueError("Google Sheets configuration is too long.")

        cleaned["credential_env"] = DEFAULT_INTEGRATIONS[integration_id][
            "credential_env"
        ]

        credential_present = bool(
            cleaned.get("credential_env")
            and os.environ.get(cleaned["credential_env"])
        )
        cleaned["status"] = "ready" if credential_present else "needs_credentials"
        cleaned["last_checked"] = datetime.now(timezone.utc).isoformat()
        organization = self.organizations.setdefault(organization_id, {})
        organization[integration_id] = cleaned
        self.save()
        return self.get_all(organization_id)[integration_id]

    def record_transfer(self, organization_id: str, action: str) -> dict[str, Any]:
        organization = self.organizations.setdefault(organization_id, {})
        excel = organization.setdefault("excel", {})
        excel.update(
            {
                "status": "ready",
                "last_sync": datetime.now(timezone.utc).isoformat(),
                "last_action": action,
            }
        )
        self.save()
        return self.get_all(organization_id)["excel"]

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as file:
            json.dump({"organizations": self.organizations}, file, indent=4)

    def _load(self) -> dict[str, dict[str, dict[str, Any]]]:
        if not self.path.exists():
            return {}
        with open(self.path, "r", encoding="utf-8") as file:
            data = json.load(file)
        return dict(data.get("organizations", {}))

    def _validate_https_endpoint(self, endpoint: str):
        if len(endpoint) > 2_048:
            raise ValueError("CRM API endpoint is too long.")
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username:
            raise ValueError("CRM API endpoint must be an HTTPS URL without credentials.")
        hostname = parsed.hostname.lower().rstrip(".")
        if hostname == "localhost" or hostname.endswith(".localhost"):
            raise ValueError("CRM API endpoint cannot target a local address.")
        try:
            address = ip_address(hostname)
        except ValueError:
            return
        if not address.is_global:
            raise ValueError("CRM API endpoint must use a public address.")
