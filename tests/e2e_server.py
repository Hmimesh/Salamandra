from __future__ import annotations

import argparse
import sys
import tempfile
import threading
from dataclasses import replace
from http.server import ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from accounts import AccountStore
from event_memory import EventMemory
from event_templates import TemplateCatalog
from integrations import IntegrationStore
from inventory_workspace import InventoryWorkspace
from Item_node import ItemNode
from item_classes import ItemClassCatalog
from kits import KitStore
from presets import PresetCatalog
from server import SalamandraServer
from web_config import WebConfig


def handler_for(data_dir: Path, port: int):
    accounts = AccountStore(data_dir / "users.json")
    accounts.create_user(
        name="Playwright Owner",
        email="owner@playwright.test",
        password="playwright-password",
        role="owner",
        organization_id="playwright-org",
        organization_name="Playwright Operations",
    )
    owner = accounts.authenticate("owner@playwright.test", "playwright-password")
    if owner is None:
        raise RuntimeError("Could not create the Playwright account fixture.")
    accounts.create_user(
        name="Playwright Technician", email="tech@playwright.test",
        password="playwright-password", role="technician",
        organization_id=owner.organization_id,
        organization_name="Playwright Operations",
    )

    workspace = InventoryWorkspace(data_dir / "inventories.json")
    workspace.add_item(
        ItemNode(id="xlr cable", type="cable", count=12, info="Balanced signal cable"),
        amount=12,
        scope="shared",
        user_id=owner.id,
        organization_id=owner.organization_id,
    )
    workspace.add_item(
        ItemNode(id="main speaker", type="pa", count=4, info="Active 15 inch PA"),
        amount=4,
        scope="shared",
        user_id=owner.id,
        organization_id=owner.organization_id,
    )

    class PlaywrightHandler(SalamandraServer):
        pass

    PlaywrightHandler.accounts = accounts
    PlaywrightHandler.workspace = workspace
    PlaywrightHandler.catalog = PresetCatalog()
    PlaywrightHandler.templates = TemplateCatalog()
    PlaywrightHandler.memory = EventMemory(data_dir / "events.json")
    PlaywrightHandler.item_classes = ItemClassCatalog(data_dir / "item_classes.json")
    PlaywrightHandler.integrations = IntegrationStore(data_dir / "integrations.json")
    PlaywrightHandler.kits = KitStore(data_dir / "kits.json")
    PlaywrightHandler.sessions = {}
    PlaywrightHandler.login_attempts = {}
    PlaywrightHandler.registration_attempts = {}
    PlaywrightHandler.event_create_requests = {}
    PlaywrightHandler.operation_lock = threading.RLock()
    PlaywrightHandler.database_runtime = None
    PlaywrightHandler.readiness_probe = None
    origin = f"http://127.0.0.1:{port}"
    PlaywrightHandler.web_config = replace(
        WebConfig.local_default(),
        canonical_origin=origin,
        allowed_origins=frozenset({origin}),
        registration_mode="disabled",
    )
    return PlaywrightHandler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4173)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="salamandra-playwright-") as directory:
        server = ThreadingHTTPServer(
            ("127.0.0.1", args.port), handler_for(Path(directory), args.port)
        )
        try:
            server.serve_forever()
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
