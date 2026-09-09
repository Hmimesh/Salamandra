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

from event_templates import TemplateCatalog
from Item_node import ItemNode
from presets import PresetCatalog
from server import SalamandraServer
from web_config import WebConfig
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from database import Base
from postgres_runtime import PostgresRuntime


def handler_for(data_dir: Path, port: int):
    engine = create_engine(f"sqlite+pysqlite:///{(data_dir / 'browser.db').as_posix()}", connect_args={"check_same_thread": False})
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    runtime = PostgresRuntime(sessionmaker(bind=engine, expire_on_commit=False))
    accounts = runtime.accounts
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
    for viewport in ("mobile-360", "tablet-768", "desktop-1280", "wide-1440", "desktop-200-percent"):
        accounts.create_user(
            name="Import Owner", email=f"owner@phase-b-{viewport}.test",
            password="playwright-password", role="owner",
            organization_id=f"import-{viewport}", organization_name=f"Import {viewport}",
        )
    accounts.create_user(
        name="Playwright Technician", email="tech@playwright.test",
        password="playwright-password", role="technician",
        organization_id=owner.organization_id,
        organization_name="Playwright Operations",
    )

    workspace = runtime.workspace
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
    PlaywrightHandler.memory = runtime.memory
    PlaywrightHandler.item_classes = runtime.item_classes
    PlaywrightHandler.integrations = runtime.integrations
    PlaywrightHandler.kits = runtime.kits
    PlaywrightHandler.sessions = {}
    PlaywrightHandler.login_attempts = {}
    PlaywrightHandler.registration_attempts = {}
    PlaywrightHandler.event_create_requests = {}
    PlaywrightHandler.operation_lock = threading.RLock()
    PlaywrightHandler.database_runtime = runtime
    PlaywrightHandler.readiness_probe = None
    origin = f"http://127.0.0.1:{port}"
    PlaywrightHandler.web_config = replace(
        WebConfig.local_default(),
        canonical_origin=origin,
        allowed_origins=frozenset({origin}),
        registration_mode="disabled",
    )
    return PlaywrightHandler


class BrowserFixtureServer(ThreadingHTTPServer):
    # Asset imports arrive in bursts; Python 3.12's default listen backlog is five.
    request_queue_size = 64


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4173)
    parser.add_argument("--parent-stdin", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="salamandra-playwright-") as directory:
        server = BrowserFixtureServer(
            ("127.0.0.1", args.port), handler_for(Path(directory), args.port)
        )
        worker = threading.Thread(target=server.serve_forever)
        worker.start()
        try:
            if args.parent_stdin:
                # EOF also arrives if the runner crashes; never expose a shutdown API.
                sys.stdin.buffer.read()
            else:
                worker.join()
        finally:
            server.shutdown()
            worker.join(timeout=10)
            server.server_close()
            server.RequestHandlerClass.database_runtime.factory.kw["bind"].dispose()


if __name__ == "__main__":
    main()
