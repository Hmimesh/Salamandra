from __future__ import annotations

import hashlib
import http.client
import json
import multiprocessing
import os
import socket
import sys
import threading
import time
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, delete, event as sqlalchemy_event, func, select, text
from sqlalchemy.orm import sessionmaker

try:
    from tests.acceptance_support import postgres_required, race_required
except ModuleNotFoundError:
    from acceptance_support import postgres_required, race_required


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from accounts import AccountStore
from database import (
    AllocationLineModel,
    AllocationModel,
    AuditEventModel,
    Base,
    EventModel,
    InventoryAdjustmentModel,
    InventoryHoldingModel,
    MembershipModel,
    OperationRequestModel,
    OrganizationModel,
    SessionModel,
    StockMovementModel,
    UserModel,
)
from readiness import DatabaseReadiness


def _request(
    port: int,
    method: str,
    path: str,
    body: dict | None = None,
    cookie: str = "",
) -> tuple[int, dict, dict[str, str]]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    if cookie:
        headers["Cookie"] = cookie
    connection.request(method, path, body=payload, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    response_headers = {name.lower(): value for name, value in response.getheaders()}
    connection.close()
    data = json.loads(raw.decode("utf-8")) if raw else {}
    return response.status, data, response_headers


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _serve_postgres(
    database_url: str,
    schema: str,
    port: int,
    ready: multiprocessing.synchronize.Event,
    stop: multiprocessing.synchronize.Event,
) -> None:
    from http.server import ThreadingHTTPServer

    from database import create_database_engine, session_factory
    from postgres_runtime import PostgresRuntime
    from server import SalamandraServer
    from web_config import WebConfig
    from event_templates import Kit, TemplateCatalog
    from Item_node import Requirement

    engine = create_database_engine(database_url, production=True)
    engine.dispose()
    engine = create_engine(
        database_url,
        future=True,
        pool_pre_ping=True,
        connect_args={"options": f"-csearch_path={schema}"},
    )
    runtime = PostgresRuntime(session_factory(engine))

    class DatabaseHandler(SalamandraServer):
        pass

    DatabaseHandler.database_runtime = runtime
    DatabaseHandler.accounts = runtime.accounts
    DatabaseHandler.workspace = runtime.workspace
    DatabaseHandler.memory = runtime.memory
    DatabaseHandler.item_classes = runtime.item_classes
    DatabaseHandler.integrations = runtime.integrations
    DatabaseHandler.templates = TemplateCatalog(
        kits=[
            Kit(
                id="atomic-kit",
                name="Atomic Kit",
                category="test",
                description="One stocked item.",
                items=(Requirement("atomic kit item", 1),),
            ),
            Kit(
                id="other-atomic-kit",
                name="Other Atomic Kit",
                category="test",
                description="Conflicting idempotency source.",
                items=(Requirement("atomic kit item", 1),),
            ),
            Kit(
                id="missing-atomic-kit",
                name="Missing Atomic Kit",
                category="test",
                description="Intentionally unavailable stock.",
                items=(Requirement("never stocked kit item", 1),),
            ),
        ]
    )
    DatabaseHandler.sessions = {}
    DatabaseHandler.login_attempts = {}
    DatabaseHandler.registration_attempts = {}
    DatabaseHandler.web_config = replace(
        WebConfig.local_default(),
        registration_mode="open",
    )
    DatabaseHandler.operation_lock = threading.RLock()
    DatabaseHandler.readiness_probe = DatabaseReadiness(engine, ROOT)

    server = ThreadingHTTPServer(("127.0.0.1", port), DatabaseHandler)
    server.timeout = 0.1
    ready.set()
    try:
        while not stop.is_set():
            server.handle_request()
    finally:
        server.server_close()
        engine.dispose()


@postgres_required
class TestPostgresHttpRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_url = os.environ["SALAMANDRA_TEST_POSTGRES_URL"]
        cls.schema = f"salamandra_http_{uuid4().hex}"
        cls.admin_engine = create_engine(cls.database_url, isolation_level="AUTOCOMMIT")
        with cls.admin_engine.connect() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        cls.engine = create_engine(
            cls.database_url,
            future=True,
            connect_args={"options": f"-csearch_path={cls.schema}"},
        )
        Base.metadata.create_all(cls.engine)
        readiness = DatabaseReadiness(cls.engine, ROOT)
        cls.expected_revision = next(iter(readiness.expected_revisions))
        with cls.engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE alembic_version "
                    "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
                )
            )
            connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                {"revision": cls.expected_revision},
            )
        cls.factory = sessionmaker(bind=cls.engine, expire_on_commit=False)
        with cls.factory.begin() as session:
            session.add(OrganizationModel(id="runtime-org", name="Runtime Organization"))
            session.add(
                UserModel(
                    id="runtime-owner",
                    email="runtime-owner@example.test",
                    name="Runtime Owner",
                    password_hash=AccountStore.hash_password("runtime-password"),
                )
            )
            session.add(
                UserModel(
                    id="runtime-other",
                    email="runtime-other@example.test",
                    name="Other Technician",
                    password_hash=AccountStore.hash_password("other-password"),
                )
            )
            session.flush()
            session.add_all(
                [
                    MembershipModel(
                    id="runtime-membership",
                    organization_id="runtime-org",
                    user_id="runtime-owner",
                    role="owner",
                    ),
                    MembershipModel(
                        id="runtime-other-membership",
                        organization_id="runtime-org",
                        user_id="runtime-other",
                        role="technician",
                    ),
                ]
            )
        cls.json_before = cls._json_hashes()
        cls.processes: list[tuple[multiprocessing.Process, multiprocessing.Event]] = []
        cls.ports = [cls._start_process(), cls._start_process()]

    @classmethod
    def tearDownClass(cls):
        for process, stop in cls.processes:
            stop.set()
            process.join(timeout=10)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        cls.engine.dispose()
        with cls.admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{cls.schema}" CASCADE'))
        cls.admin_engine.dispose()

    @classmethod
    def _start_process(cls) -> int:
        port = _free_port()
        ready = multiprocessing.Event()
        stop = multiprocessing.Event()
        process = multiprocessing.Process(
            target=_serve_postgres,
            args=(cls.database_url, cls.schema, port, ready, stop),
            daemon=True,
        )
        process.start()
        if not ready.wait(timeout=15):
            process.terminate()
            raise RuntimeError("PostgreSQL API test process did not start.")
        cls.processes.append((process, stop))
        return port

    @staticmethod
    def _json_hashes() -> dict[str, str]:
        paths = [
            ROOT / "docs" / name
            for name in (
                "users.json",
                "inventories.json",
                "events.json",
                "item_classes.json",
                "integrations.json",
            )
        ]
        return {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths
            if path.exists()
        }

    def _sign_in(self, port: int) -> str:
        status, _, headers = _request(
            port,
            "POST",
            "/api/auth/signin",
            {
                "email": "runtime-owner@example.test",
                "password": "runtime-password",
            },
        )
        self.assertEqual(status, 200)
        return headers["set-cookie"].split(";", 1)[0]

    def _sign_in_other(self, port: int) -> str:
        status, _, headers = _request(
            port,
            "POST",
            "/api/auth/signin",
            {
                "email": "runtime-other@example.test",
                "password": "other-password",
            },
        )
        self.assertEqual(status, 200)
        return headers["set-cookie"].split(";", 1)[0]

    def test_workspace_registration_creates_an_empty_authoritative_workspace(self):
        email = f"new-owner-{uuid4().hex}@example.test"
        status, payload, headers = _request(
            self.ports[0],
            "POST",
            "/api/auth/register",
            {
                "name": "New Owner",
                "email": f"  {email.upper()}  ",
                "password": "a valid registration password",
                "organization_name": "Signal Works",
                "accept_terms": True,
                "role": "admin",
                "organization_id": "forged-organization",
                "user_id": "forged-user",
            },
        )

        self.assertEqual(status, 201)
        self.assertIn("salamandra_session=", headers["set-cookie"])
        state = payload["state"]
        registered = state["auth"]["user"]
        self.assertEqual(registered["email"], email)
        self.assertEqual(registered["role"], "owner")
        self.assertNotEqual(registered["id"], "forged-user")
        self.assertNotEqual(registered["organization_id"], "forged-organization")
        self.assertEqual(state["organization"]["name"], "Signal Works")
        self.assertEqual(state["auth"]["users"], [registered])
        self.assertEqual(state["inventory"]["items"], [])
        self.assertEqual(state["events"]["events"], [])
        self.assertNotIn("password", json.dumps(payload).lower())

        with self.factory() as session:
            user = session.scalar(select(UserModel).where(UserModel.email == email))
            self.assertIsNotNone(user)
            membership = session.scalar(
                select(MembershipModel).where(MembershipModel.user_id == user.id)
            )
            self.assertEqual(membership.role, "owner")
            self.assertEqual(membership.organization_id, registered["organization_id"])
            self.assertEqual(
                session.scalar(
                    select(func.count()).select_from(InventoryHoldingModel).where(
                        InventoryHoldingModel.organization_id
                        == registered["organization_id"]
                    )
                ),
                0,
            )
        with self.factory.begin() as session:
            session.execute(
                delete(SessionModel).where(SessionModel.user_id == registered["id"])
            )
            self.assertEqual(
                session.scalar(
                    select(func.count()).select_from(EventModel).where(
                        EventModel.organization_id == registered["organization_id"]
                    )
                ),
                0,
            )

    @race_required
    def test_concurrent_normalized_email_registration_commits_once(self):
        email = f"registration-race-{uuid4().hex}@example.test"
        barrier = threading.Barrier(2)
        results: list[tuple[int, dict, dict[str, str]]] = []
        result_lock = threading.Lock()

        def register(port: int, supplied_email: str) -> None:
            barrier.wait(timeout=5)
            result = _request(
                port,
                "POST",
                "/api/auth/register",
                {
                    "name": "Race Owner",
                    "email": supplied_email,
                    "password": "race registration password",
                    "organization_name": "One Race Workspace",
                    "accept_terms": True,
                },
            )
            with result_lock:
                results.append(result)

        threads = [
            threading.Thread(target=register, args=(self.ports[0], email.upper())),
            threading.Thread(target=register, args=(self.ports[1], f" {email} ")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        self.assertEqual(len(results), 2)
        self.assertEqual(sorted(status for status, _, _ in results), [201, 409])
        with self.factory() as session:
            users = list(
                session.scalars(
                    select(UserModel).where(func.lower(UserModel.email) == email)
                )
            )
            self.assertEqual(len(users), 1)
            memberships = list(
                session.scalars(
                    select(MembershipModel).where(
                        MembershipModel.user_id == users[0].id
                    )
                )
            )
            self.assertEqual(len(memberships), 1)
            self.assertEqual(memberships[0].role, "owner")
            self.assertEqual(
                session.scalar(
                    select(func.count()).select_from(OrganizationModel).where(
                        OrganizationModel.name == "One Race Workspace"
                    )
                ),
                1,
            )
        with self.factory.begin() as session:
            session.execute(
                delete(SessionModel).where(SessionModel.user_id == users[0].id)
            )

    def test_registration_failure_rolls_back_user_organization_and_membership(self):
        from postgres_runtime import PostgresAccountStore

        email = f"rollback-{uuid4().hex}@example.test"
        workspace_name = f"Rollback Workspace {uuid4().hex}"

        def reject_membership(_mapper, _connection, target):
            if target.role == "owner":
                raise RuntimeError("forced membership failure")

        sqlalchemy_event.listen(MembershipModel, "before_insert", reject_membership)
        try:
            with self.assertRaisesRegex(RuntimeError, "forced membership failure"):
                PostgresAccountStore(self.factory).register_workspace(
                    name="Rollback Owner",
                    email=email,
                    password="rollback registration password",
                    organization_name=workspace_name,
                )
        finally:
            sqlalchemy_event.remove(MembershipModel, "before_insert", reject_membership)

        with self.factory() as session:
            self.assertIsNone(
                session.scalar(select(UserModel).where(UserModel.email == email))
            )
            self.assertIsNone(
                session.scalar(
                    select(OrganizationModel).where(
                        OrganizationModel.name == workspace_name
                    )
                )
            )

    def test_registration_rejects_malformed_and_oversized_fields_without_writes(self):
        from postgres_runtime import PostgresAccountStore

        store = PostgresAccountStore(self.factory)
        before = self._registration_row_counts()
        cases = (
            {"name": "", "email": "valid@example.test", "password": "valid password", "organization_name": "Workspace"},
            {"name": "Owner", "email": "not-an-email", "password": "valid password", "organization_name": "Workspace"},
            {"name": "Owner", "email": "valid@example.test", "password": "short", "organization_name": "Workspace"},
            {"name": "Owner", "email": "valid@example.test", "password": "valid password", "organization_name": "W" * 201},
        )
        for values in cases:
            with self.subTest(values={key: len(value) for key, value in values.items()}):
                with self.assertRaises(ValueError):
                    store.register_workspace(**values)
        self.assertEqual(self._registration_row_counts(), before)

    def _registration_row_counts(self) -> tuple[int, int, int]:
        with self.factory() as session:
            return (
                session.scalar(select(func.count()).select_from(UserModel)),
                session.scalar(select(func.count()).select_from(OrganizationModel)),
                session.scalar(select(func.count()).select_from(MembershipModel)),
            )

    @staticmethod
    def _definition_body(
        item_id: str,
        *,
        original_id: str | None = None,
        item_type: str = "microphone",
        class_id: str = "vocal-microphone",
        info: str = "Original inventory note.",
        scope: str = "shared",
        idempotency_key: str = "",
    ) -> dict:
        body = {
            "id": item_id,
            "original_id": original_id or item_id,
            "type": item_type,
            "amount": 1,
            "scope": scope,
            "info": info,
            "class_id": class_id,
            "manufacturer": "Salamandra Test",
            "model": "Definition Fixture",
            "condition": "ready",
            "quality_score": 70,
            "preference_score": 60,
            "weight_kg": 1.5,
            "requirements": [],
        }
        if idempotency_key:
            body["idempotency_key"] = idempotency_key
        return body

    def _seed_operational_item(
        self,
        port: int,
        cookie: str,
        item_id: str,
        event_id: str,
        target_state: str,
    ) -> str:
        add_body = self._definition_body(item_id)
        add_body.pop("original_id")
        add_body["idempotency_key"] = f"add-{item_id}"
        status, payload, _ = _request(
            port,
            "POST",
            "/api/inventory/items",
            add_body,
            cookie,
        )
        self.assertEqual(status, 200, payload)
        with self.factory.begin() as session:
            session.add(
                EventModel(
                    id=event_id,
                    organization_id="runtime-org",
                    owner_user_id="runtime-owner",
                    title=event_id,
                    status="planning",
                    data={
                        "id": event_id,
                        "title": event_id,
                        "organization_id": "runtime-org",
                        "owner_id": "runtime-owner",
                        "status": "planning",
                        "plan_verified": True,
                        "plan": {
                            "lines": [
                                {"item_id": item_id, "amount": 1, "missing": 0}
                            ]
                        },
                        "checklist": [],
                        "return_checklist": [],
                        "conflicts": [],
                        "history": [],
                    },
                )
            )
        transitions = {
            "planning": (),
            "reserved": ("confirmed",),
            "packed": ("confirmed", "packed"),
            "dispatched": ("confirmed", "packed", "out"),
        }[target_state]
        for next_status in transitions:
            status, payload, _ = _request(
                port,
                "POST",
                "/api/events/status",
                {"event_id": event_id, "status": next_status},
                cookie,
            )
            self.assertEqual(status, 200, payload)
        with self.factory() as session:
            holding_id = session.scalar(
                select(InventoryHoldingModel.id).where(
                    InventoryHoldingModel.organization_id == "runtime-org",
                    InventoryHoldingModel.legacy_item_id == item_id,
                )
            )
        self.assertIsNotNone(holding_id)
        return str(holding_id)

    def _operational_snapshot(self, holding_id: str, event_id: str) -> dict:
        with self.factory() as session:
            holding = session.get(InventoryHoldingModel, holding_id)
            event = session.get(EventModel, event_id)
            allocation = session.scalar(
                select(AllocationModel).where(AllocationModel.event_id == event_id)
            )
            lines = (
                list(
                    session.scalars(
                        select(AllocationLineModel)
                        .where(AllocationLineModel.allocation_id == allocation.id)
                        .order_by(AllocationLineModel.id)
                    )
                )
                if allocation is not None
                else []
            )
            movements = list(
                session.scalars(
                    select(StockMovementModel)
                    .where(StockMovementModel.event_id == event_id)
                    .order_by(StockMovementModel.created_at, StockMovementModel.id)
                )
            )
            audits = list(
                session.scalars(
                    select(AuditEventModel)
                    .where(
                        AuditEventModel.organization_id == "runtime-org",
                        (
                            (AuditEventModel.resource_id == holding_id)
                            | (AuditEventModel.resource_id == event_id)
                        ),
                    )
                    .order_by(AuditEventModel.created_at, AuditEventModel.id)
                )
            )
            return {
                "holding": {
                    "identity": holding.legacy_item_id,
                    "scope": holding.scope,
                    "owner": holding.owner_user_id,
                    "data": deepcopy(holding.data),
                    "buckets": (
                        holding.available_quantity,
                        holding.reserved_quantity,
                        holding.packed_quantity,
                        holding.dispatched_quantity,
                    ),
                    "active": holding.active,
                    "version": holding.version,
                },
                "event": {
                    "status": event.status,
                    "data": deepcopy(event.data),
                    "version": event.version,
                },
                "allocation": (
                    None
                    if allocation is None
                    else {
                        "id": allocation.id,
                        "status": allocation.status,
                        "version": allocation.version,
                    }
                ),
                "lines": [
                    {
                        "id": line.id,
                        "holding_id": line.holding_id,
                        "quantity": line.quantity,
                        "state": line.state,
                    }
                    for line in lines
                ],
                "movements": [
                    {
                        "id": movement.id,
                        "action": movement.action,
                        "key": movement.idempotency_key,
                        "lines": deepcopy(movement.lines),
                    }
                    for movement in movements
                ],
                "audits": [
                    {
                        "id": audit.id,
                        "action": audit.action,
                        "resource_id": audit.resource_id,
                        "request_id": audit.request_id,
                        "changes": deepcopy(audit.changes),
                    }
                    for audit in audits
                ],
            }

    def test_active_definition_changes_are_rejected_without_partial_mutation(self):
        port = self.ports[0]
        cookie = self._sign_in(port)
        for target_state in ("reserved", "packed", "dispatched"):
            item_id = f"guarded-{target_state}-item"
            event_id = f"guarded-{target_state}-event"
            holding_id = self._seed_operational_item(
                port,
                cookie,
                item_id,
                event_id,
                target_state,
            )
            before = self._operational_snapshot(holding_id, event_id)
            operation_key = f"redefine-{target_state}-holding"

            status, _, _ = _request(
                port,
                "POST",
                "/api/inventory/items/update",
                self._definition_body(
                    f"renamed-{target_state}-item",
                    original_id=item_id,
                    item_type="pa",
                    class_id="main-pa",
                    idempotency_key=operation_key,
                ),
                cookie,
            )

            self.assertEqual(status, 409)
            self.assertEqual(self._operational_snapshot(holding_id, event_id), before)
            with self.factory() as session:
                failed_request_count = session.scalar(
                    select(func.count())
                    .select_from(OperationRequestModel)
                    .where(
                        OperationRequestModel.organization_id == "runtime-org",
                        OperationRequestModel.idempotency_key == operation_key,
                    )
                )
            self.assertEqual(failed_request_count, 0)
        _request(port, "POST", "/api/auth/signout", {}, cookie)

    def test_free_and_harmless_definition_updates_are_audited_idempotently(self):
        port = self.ports[0]
        cookie = self._sign_in(port)
        free_item_id = "free-definition-item"
        add_body = self._definition_body(free_item_id)
        add_body.pop("original_id")
        add_body["idempotency_key"] = "add-free-definition-item"
        status, _, _ = _request(
            port, "POST", "/api/inventory/items", add_body, cookie
        )
        self.assertEqual(status, 200)
        with self.factory() as session:
            free_holding_id = session.scalar(
                select(InventoryHoldingModel.id).where(
                    InventoryHoldingModel.organization_id == "runtime-org",
                    InventoryHoldingModel.legacy_item_id == free_item_id,
                )
            )

        update_body = self._definition_body(
            "renamed-free-definition-item",
            original_id=free_item_id,
            item_type="pa",
            class_id="main-pa",
            info="Corrected free definition.",
            idempotency_key="update-free-definition-item",
        )
        first_status, _, _ = _request(
            port,
            "POST",
            "/api/inventory/items/update",
            update_body,
            cookie,
        )
        retry_status, _, _ = _request(
            port,
            "POST",
            "/api/inventory/items/update",
            update_body,
            cookie,
        )
        conflict_body = dict(update_body)
        conflict_body["info"] = "Conflicting retry."
        conflict_status, _, _ = _request(
            port,
            "POST",
            "/api/inventory/items/update",
            conflict_body,
            cookie,
        )
        self.assertEqual((first_status, retry_status, conflict_status), (200, 200, 409))

        with self.factory() as session:
            free_holding = session.get(InventoryHoldingModel, free_holding_id)
            free_audits = list(
                session.scalars(
                    select(AuditEventModel).where(
                        AuditEventModel.resource_id == free_holding_id,
                        AuditEventModel.action == "inventory.definition_updated",
                    )
                )
            )
            free_requests = session.scalar(
                select(func.count())
                .select_from(OperationRequestModel)
                .where(
                    OperationRequestModel.organization_id == "runtime-org",
                    OperationRequestModel.operation == "inventory.definition_update",
                    OperationRequestModel.idempotency_key
                    == "update-free-definition-item",
                )
            )
            version_after_update = free_holding.version
        self.assertEqual(free_holding.legacy_item_id, "renamed-free-definition-item")
        self.assertEqual(free_holding.data["class_id"], "main-pa")
        self.assertEqual(len(free_audits), 1)
        self.assertEqual(free_requests, 1)
        self.assertEqual(
            set(free_audits[0].changes["changed_fields"]),
            {"item_id", "type", "class_id", "info"},
        )
        self.assertEqual(
            free_audits[0].changes["before"]["item_id"],
            free_item_id,
        )
        self.assertEqual(
            free_audits[0].changes["after"]["item_id"],
            "renamed-free-definition-item",
        )

        no_op_body = dict(update_body)
        no_op_body["original_id"] = "renamed-free-definition-item"
        no_op_body["idempotency_key"] = "no-op-free-definition-item"
        no_op_status, _, _ = _request(
            port,
            "POST",
            "/api/inventory/items/update",
            no_op_body,
            cookie,
        )
        self.assertEqual(no_op_status, 200)
        with self.factory() as session:
            free_holding = session.get(InventoryHoldingModel, free_holding_id)
            free_audit_count = session.scalar(
                select(func.count())
                .select_from(AuditEventModel)
                .where(
                    AuditEventModel.resource_id == free_holding_id,
                    AuditEventModel.action == "inventory.definition_updated",
                )
            )
        self.assertEqual(free_holding.version, version_after_update)
        self.assertEqual(free_audit_count, 1)

        active_item_id = "harmless-active-item"
        active_event_id = "harmless-active-event"
        active_holding_id = self._seed_operational_item(
            port,
            cookie,
            active_item_id,
            active_event_id,
            "reserved",
        )
        active_before = self._operational_snapshot(
            active_holding_id,
            active_event_id,
        )
        harmless_status, _, _ = _request(
            port,
            "POST",
            "/api/inventory/items/update",
            self._definition_body(
                active_item_id,
                original_id=active_item_id,
                info="Updated operator note only.",
                idempotency_key="harmless-active-note",
            ),
            cookie,
        )
        self.assertEqual(harmless_status, 200)
        active_after = self._operational_snapshot(
            active_holding_id,
            active_event_id,
        )
        self.assertEqual(active_after["holding"]["identity"], active_item_id)
        self.assertEqual(active_after["holding"]["buckets"], active_before["holding"]["buckets"])
        self.assertEqual(active_after["event"], active_before["event"])
        self.assertEqual(active_after["allocation"], active_before["allocation"])
        self.assertEqual(active_after["lines"], active_before["lines"])
        self.assertEqual(active_after["movements"], active_before["movements"])
        new_audits = active_after["audits"][len(active_before["audits"]) :]
        self.assertEqual(len(new_audits), 1)
        self.assertEqual(new_audits[0]["action"], "inventory.definition_updated")
        self.assertEqual(new_audits[0]["changes"]["changed_fields"], ["info"])
        _request(port, "POST", "/api/auth/signout", {}, cookie)

    def test_definition_update_tenant_and_permission_boundaries(self):
        port = self.ports[0]
        owner_cookie = self._sign_in(port)
        with self.factory.begin() as session:
            session.add(OrganizationModel(id="definition-org-b", name="Definition Org B"))
            session.add(
                UserModel(
                    id="definition-owner-b",
                    email="definition-owner-b@example.test",
                    name="Definition Owner B",
                    password_hash=AccountStore.hash_password("definition-password"),
                )
            )
            session.flush()
            session.add(
                MembershipModel(
                    id="definition-membership-b",
                    organization_id="definition-org-b",
                    user_id="definition-owner-b",
                    role="owner",
                )
            )
            session.add(
                InventoryHoldingModel(
                    id="definition-foreign-holding",
                    organization_id="definition-org-b",
                    legacy_item_id="foreign-definition-item",
                    scope="shared",
                    available_quantity=1,
                    data={"type": "microphone", "info": "Org B only."},
                )
            )
        foreign_status, _, _ = _request(
            port,
            "POST",
            "/api/inventory/items/update",
            self._definition_body(
                "foreign-definition-renamed",
                original_id="foreign-definition-item",
                idempotency_key="cross-org-definition-update",
            ),
            owner_cookie,
        )
        self.assertEqual(foreign_status, 404)
        with self.factory() as session:
            foreign_holding = session.get(
                InventoryHoldingModel,
                "definition-foreign-holding",
            )
            foreign_audits = session.scalar(
                select(func.count())
                .select_from(AuditEventModel)
                .where(AuditEventModel.resource_id == "definition-foreign-holding")
            )
        self.assertEqual(foreign_holding.legacy_item_id, "foreign-definition-item")
        self.assertEqual(foreign_holding.data, {"type": "microphone", "info": "Org B only."})
        self.assertEqual(foreign_audits, 0)

        technician_cookie = self._sign_in_other(port)
        personal_body = self._definition_body(
            "technician-definition-item",
            scope="personal",
        )
        personal_body.pop("original_id")
        personal_body["idempotency_key"] = "add-technician-definition-item"
        add_status, _, _ = _request(
            port,
            "POST",
            "/api/inventory/items",
            personal_body,
            technician_cookie,
        )
        self.assertEqual(add_status, 200)
        denied_status, _, _ = _request(
            port,
            "POST",
            "/api/inventory/items/update",
            self._definition_body(
                "technician-definition-renamed",
                original_id="technician-definition-item",
                scope="personal",
                idempotency_key="technician-definition-update",
            ),
            technician_cookie,
        )
        self.assertEqual(denied_status, 403)
        with self.factory() as session:
            personal_holding = session.scalar(
                select(InventoryHoldingModel).where(
                    InventoryHoldingModel.organization_id == "runtime-org",
                    InventoryHoldingModel.owner_user_id == "runtime-other",
                    InventoryHoldingModel.legacy_item_id
                    == "technician-definition-item",
                )
            )
            denied_audits = session.scalar(
                select(func.count())
                .select_from(AuditEventModel)
                .where(
                    AuditEventModel.resource_id == personal_holding.id,
                    AuditEventModel.action == "inventory.definition_updated",
                )
            )
        self.assertEqual(personal_holding.legacy_item_id, "technician-definition-item")
        self.assertEqual(denied_audits, 0)
        _request(port, "POST", "/api/auth/signout", {}, owner_cookie)
        _request(port, "POST", "/api/auth/signout", {}, technician_cookie)

    @race_required
    def test_adjacent_definition_bypasses_and_reservation_race_are_safe(self):
        first_port, second_port = self.ports
        cookie = self._sign_in(first_port)
        item_id = "adjacent-guard-item"
        event_id = "adjacent-guard-event"
        holding_id = self._seed_operational_item(
            first_port,
            cookie,
            item_id,
            event_id,
            "reserved",
        )
        before = self._operational_snapshot(holding_id, event_id)

        changed_add = self._definition_body(
            item_id,
            item_type="pa",
            class_id="main-pa",
        )
        changed_add.pop("original_id")
        changed_add["idempotency_key"] = "active-definition-add-bypass"
        add_status, _, _ = _request(
            first_port,
            "POST",
            "/api/inventory/items",
            changed_add,
            cookie,
        )
        csv_status, _, _ = _request(
            first_port,
            "POST",
            "/api/inventory/import.csv",
            {
                "scope": "shared",
                "idempotency_key": "active-definition-import-bypass",
                "csv": (
                    "id,count,type,class_id,info,manufacturer,model,condition,"
                    "quality_score,preference_score,weight_kg\n"
                    f"{item_id},0,pa,main-pa,Changed by CSV,Salamandra Test,"
                    "Definition Fixture,ready,70,60,1.5\n"
                ),
            },
            cookie,
        )
        self.assertEqual((add_status, csv_status), (409, 409))
        self.assertEqual(self._operational_snapshot(holding_id, event_id), before)

        race_item_id = "definition-reservation-race-item"
        race_event_id = "definition-reservation-race-event"
        race_holding_id = self._seed_operational_item(
            first_port,
            cookie,
            race_item_id,
            race_event_id,
            "planning",
        )
        barrier = threading.Barrier(3)
        results: list[tuple[str, int]] = []

        def redefine():
            barrier.wait(timeout=10)
            status, _, _ = _request(
                first_port,
                "POST",
                "/api/inventory/items/update",
                self._definition_body(
                    "definition-reservation-race-renamed",
                    original_id=race_item_id,
                    item_type="pa",
                    class_id="main-pa",
                    idempotency_key="definition-reservation-race-update",
                ),
                cookie,
            )
            results.append(("update", status))

        def reserve():
            barrier.wait(timeout=10)
            status, _, _ = _request(
                second_port,
                "POST",
                "/api/events/status",
                {"event_id": race_event_id, "status": "confirmed"},
                cookie,
            )
            results.append(("reserve", status))

        threads = [threading.Thread(target=redefine), threading.Thread(target=reserve)]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=10)
        for thread in threads:
            thread.join(timeout=15)
        self.assertEqual(sorted(status for _, status in results), [200, 409])

        with self.factory() as session:
            holding = session.get(InventoryHoldingModel, race_holding_id)
            event = session.get(EventModel, race_event_id)
            allocation_count = session.scalar(
                select(func.count())
                .select_from(AllocationModel)
                .where(AllocationModel.event_id == race_event_id)
            )
            definition_audit_count = session.scalar(
                select(func.count())
                .select_from(AuditEventModel)
                .where(
                    AuditEventModel.resource_id == race_holding_id,
                    AuditEventModel.action == "inventory.definition_updated",
                )
            )
        if event.status == "confirmed":
            self.assertEqual(holding.legacy_item_id, race_item_id)
            self.assertEqual(
                (holding.available_quantity, holding.reserved_quantity),
                (0, 1),
            )
            self.assertEqual((allocation_count, definition_audit_count), (1, 0))
        else:
            self.assertEqual(event.status, "planning")
            self.assertEqual(
                holding.legacy_item_id,
                "definition-reservation-race-renamed",
            )
            self.assertEqual(
                (
                    holding.available_quantity,
                    holding.reserved_quantity,
                    holding.packed_quantity,
                    holding.dispatched_quantity,
                ),
                (1, 0, 0, 0),
            )
            self.assertEqual((allocation_count, definition_audit_count), (0, 1))
        _request(first_port, "POST", "/api/auth/signout", {}, cookie)

    def test_health_and_database_schema_readiness(self):
        port = self.ports[0]
        health_status, health_payload, _ = _request(port, "GET", "/health")
        ready_status, ready_payload, _ = _request(port, "GET", "/ready")
        self.assertEqual((health_status, health_payload), (200, {"status": "ok"}))
        self.assertEqual((ready_status, ready_payload), (200, {"status": "ready"}))

        try:
            with self.engine.begin() as connection:
                connection.execute(
                    text("UPDATE alembic_version SET version_num = 'wrong-revision'")
                )
            wrong_status, wrong_payload, _ = _request(port, "GET", "/ready")
            self.assertEqual(wrong_status, 503)
            self.assertEqual(wrong_payload, {"status": "not_ready"})
        finally:
            with self.engine.begin() as connection:
                connection.execute(
                    text("UPDATE alembic_version SET version_num = :revision"),
                    {"revision": self.expected_revision},
                )

    @race_required
    def test_atomic_kit_checkout_race_and_rollback(self):
        first_port, second_port = self.ports
        cookie = self._sign_in(first_port)
        status, _, _ = _request(
            first_port,
            "POST",
            "/api/inventory/items",
            {
                "id": "atomic kit item",
                "type": "other",
                "amount": 2,
                "scope": "shared",
            },
            cookie,
        )
        self.assertEqual(status, 200)

        start = threading.Barrier(3)
        results: list[tuple[int, dict]] = []
        body = {
            "source_id": "atomic-kit",
            "idempotency_key": "atomic-kit-race-key",
        }

        def checkout(port: int):
            start.wait(timeout=10)
            status_code, payload, _ = _request(
                port, "POST", "/api/kits/checkout", body, cookie
            )
            results.append((status_code, payload))

        threads = [
            threading.Thread(target=checkout, args=(first_port,)),
            threading.Thread(target=checkout, args=(second_port,)),
        ]
        for thread in threads:
            thread.start()
        start.wait(timeout=10)
        for thread in threads:
            thread.join(timeout=15)

        self.assertEqual(sorted(status for status, _ in results), [200, 201])
        event_ids = {payload["event"]["id"] for _, payload in results}
        self.assertEqual(len(event_ids), 1)
        event_id = event_ids.pop()
        with self.factory() as session:
            event = session.get(EventModel, event_id)
            holding = session.scalar(
                select(InventoryHoldingModel).where(
                    InventoryHoldingModel.organization_id == "runtime-org",
                    InventoryHoldingModel.legacy_item_id == "atomic kit item",
                )
            )
            allocation = session.scalar(
                select(AllocationModel).where(AllocationModel.event_id == event_id)
            )
            request_count = session.scalar(
                select(func.count()).select_from(OperationRequestModel).where(
                    OperationRequestModel.organization_id == "runtime-org",
                    OperationRequestModel.idempotency_key == "atomic-kit-race-key",
                )
            )
            movement_count = session.scalar(
                select(func.count()).select_from(StockMovementModel).where(
                    StockMovementModel.event_id == event_id
                )
            )
            checkout_audits = session.scalar(
                select(func.count()).select_from(AuditEventModel).where(
                    AuditEventModel.resource_id == event_id,
                    AuditEventModel.action == "kit.checkout",
                )
            )
        self.assertEqual(event.status, "out")
        self.assertEqual(allocation.status, "dispatched")
        self.assertEqual(
            (holding.available_quantity, holding.dispatched_quantity), (1, 1)
        )
        self.assertEqual(request_count, 1)
        self.assertEqual(movement_count, 3)
        self.assertEqual(checkout_audits, 1)

        conflict_status, _, _ = _request(
            first_port,
            "POST",
            "/api/kits/checkout",
            {
                "source_id": "other-atomic-kit",
                "idempotency_key": "atomic-kit-race-key",
            },
            cookie,
        )
        self.assertEqual(conflict_status, 409)

        rollback_status, _, _ = _request(
            first_port,
            "POST",
            "/api/kits/checkout",
            {
                "source_id": "missing-atomic-kit",
                "idempotency_key": "atomic-kit-rollback-key",
            },
            cookie,
        )
        self.assertEqual(rollback_status, 409)
        with self.factory() as session:
            rollback_requests = session.scalar(
                select(func.count()).select_from(OperationRequestModel).where(
                    OperationRequestModel.idempotency_key == "atomic-kit-rollback-key"
                )
            )
            rollback_events = session.scalar(
                select(func.count()).select_from(EventModel).where(
                    EventModel.data["source_id"].as_string() == "missing-atomic-kit"
                )
            )
        self.assertEqual((rollback_requests, rollback_events), (0, 0))
        _request(first_port, "POST", "/api/auth/signout", {}, cookie)

    def test_direct_inventory_use_and_return_are_disabled_in_production(self):
        first_port = self.ports[0]
        cookie = self._sign_in(first_port)
        status, _, _ = _request(
            first_port,
            "POST",
            "/api/inventory/items",
            {
                "id": "manual movement item",
                "type": "other",
                "amount": 2,
                "scope": "shared",
            },
            cookie,
        )
        self.assertEqual(status, 200)
        with self.factory() as session:
            movement_count = session.scalar(
                select(func.count()).select_from(StockMovementModel)
            )

        statuses = [
            _request(
                first_port,
                "POST",
                path,
                {
                    "item_id": "manual movement item",
                    "amount": 1,
                    "scope": "shared",
                    "idempotency_key": "ignored-legacy-key",
                    "reason": "test",
                },
                cookie,
            )[0]
            for path in ("/api/inventory/use", "/api/inventory/return")
        ]
        with self.factory() as session:
            holding = session.scalar(
                select(InventoryHoldingModel).where(
                    InventoryHoldingModel.organization_id == "runtime-org",
                    InventoryHoldingModel.legacy_item_id == "manual movement item",
                )
            )
            after_movements = session.scalar(
                select(func.count()).select_from(StockMovementModel)
            )
        self.assertEqual(statuses, [409, 409])
        self.assertEqual(
            (holding.available_quantity, holding.dispatched_quantity), (2, 0)
        )
        self.assertEqual(after_movements, movement_count)
        _request(first_port, "POST", "/api/auth/signout", {}, cookie)

    @race_required
    def test_event_checklist_and_return_never_overwrite_operational_state(self):
        first_port, second_port = self.ports
        cookie = self._sign_in(first_port)
        status, _, _ = _request(
            first_port,
            "POST",
            "/api/inventory/items",
            {
                "id": "stale event race item",
                "type": "other",
                "amount": 1,
                "scope": "shared",
                "idempotency_key": "stale-event-race-stock",
            },
            cookie,
        )
        self.assertEqual(status, 200)

        def create_out_event(event_id: str):
            with self.factory.begin() as session:
                session.add(
                    EventModel(
                        id=event_id,
                        organization_id="runtime-org",
                        owner_user_id="runtime-owner",
                        title=event_id,
                        status="planning",
                        data={
                            "id": event_id,
                            "organization_id": "runtime-org",
                            "owner_id": "runtime-owner",
                            "title": event_id,
                            "status": "planning",
                            "plan_verified": True,
                            "plan": {
                                "lines": [
                                    {
                                        "item_id": "stale event race item",
                                        "amount": 1,
                                        "missing": 0,
                                    }
                                ]
                            },
                            "checklist": [
                                {
                                    "id": f"{event_id}-pack",
                                    "item_id": "stale event race item",
                                    "amount": 1,
                                    "phase": "pack",
                                    "done": True,
                                }
                            ],
                            "return_checklist": [
                                {
                                    "id": f"{event_id}-return",
                                    "item_id": "stale event race item",
                                    "amount": 1,
                                    "phase": "return",
                                    "done": True,
                                }
                            ],
                            "history": [],
                            "conflicts": [],
                        },
                    )
                )
            for next_status in ("confirmed", "packed", "out"):
                status_code, payload, _ = _request(
                    first_port,
                    "POST",
                    "/api/events/status",
                    {"event_id": event_id, "status": next_status},
                    cookie,
                )
                self.assertEqual(status_code, 200, payload)

        def update_checklist(port: int, event_id: str) -> tuple[int, dict]:
            status_code, payload, _ = _request(
                port,
                "POST",
                "/api/events/checklist",
                {
                    "event_id": event_id,
                    "phase": "pack",
                    "item_id": "stale event race item",
                    "done": False,
                },
                cookie,
            )
            return status_code, payload

        def return_event(port: int, event_id: str) -> tuple[int, dict]:
            status_code, payload, _ = _request(
                port,
                "POST",
                "/api/events/status",
                {"event_id": event_id, "status": "returned"},
                cookie,
            )
            return status_code, payload

        create_out_event("checklist-before-return")
        self.assertEqual(update_checklist(first_port, "checklist-before-return")[0], 200)
        self.assertEqual(return_event(second_port, "checklist-before-return")[0], 200)

        create_out_event("return-before-checklist")
        self.assertEqual(return_event(second_port, "return-before-checklist")[0], 200)
        self.assertEqual(update_checklist(first_port, "return-before-checklist")[0], 200)

        create_out_event("checklist-return-race")
        barrier = threading.Barrier(3)
        results: list[tuple[int, dict]] = []

        def race(action, port: int):
            barrier.wait(timeout=10)
            results.append(action(port, "checklist-return-race"))

        threads = [
            threading.Thread(target=race, args=(update_checklist, first_port)),
            threading.Thread(target=race, args=(return_event, second_port)),
        ]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=10)
        for thread in threads:
            thread.join(timeout=15)
        self.assertEqual(sorted(status_code for status_code, _ in results), [200, 200])

        with self.factory() as session:
            for event_id in (
                "checklist-before-return",
                "return-before-checklist",
                "checklist-return-race",
            ):
                event = session.get(EventModel, event_id)
                allocation = session.scalar(
                    select(AllocationModel).where(AllocationModel.event_id == event_id)
                )
                return_count = session.scalar(
                    select(func.count()).select_from(StockMovementModel).where(
                        StockMovementModel.event_id == event_id,
                        StockMovementModel.action == "returned",
                    )
                )
                self.assertEqual(event.status, "returned")
                self.assertEqual(allocation.status, "returned")
                self.assertFalse(event.data["checklist"][0]["done"])
                self.assertEqual(return_count, 1)
            holding = session.scalar(
                select(InventoryHoldingModel).where(
                    InventoryHoldingModel.organization_id == "runtime-org",
                    InventoryHoldingModel.legacy_item_id == "stale event race item",
                )
            )
        self.assertEqual(
            (holding.available_quantity, holding.dispatched_quantity), (1, 0)
        )
        _request(first_port, "POST", "/api/auth/signout", {}, cookie)

    @race_required
    def test_inventory_reconciliation_http_ledger_idempotency_and_race(self):
        first_port, second_port = self.ports
        cookie = self._sign_in(first_port)
        request_body = {
            "id": "ledger race item",
            "type": "other",
            "amount": 1,
            "scope": "shared",
            "idempotency_key": "ledger-race-same-key",
        }
        barrier = threading.Barrier(3)
        results: list[int] = []

        def add_once(port: int):
            barrier.wait(timeout=10)
            results.append(
                _request(
                    port,
                    "POST",
                    "/api/inventory/items",
                    request_body,
                    cookie,
                )[0]
            )

        threads = [
            threading.Thread(target=add_once, args=(first_port,)),
            threading.Thread(target=add_once, args=(second_port,)),
        ]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=10)
        for thread in threads:
            thread.join(timeout=15)
        self.assertEqual(results, [200, 200])

        conflict_status, _, _ = _request(
            first_port,
            "POST",
            "/api/inventory/items",
            {**request_body, "amount": 2},
            cookie,
        )
        self.assertEqual(conflict_status, 409)

        for key in ("ledger-independent-a", "ledger-independent-b"):
            request_body_copy = {**request_body, "idempotency_key": key}
            status, _, _ = _request(
                first_port if key.endswith("a") else second_port,
                "POST",
                "/api/inventory/items",
                request_body_copy,
                cookie,
            )
            self.assertEqual(status, 200)

        import_body = {
            "csv": "id,type,count\nledger race item,other,5\n",
            "scope": "shared",
            "idempotency_key": "ledger-import-five",
        }
        for port in (first_port, second_port):
            status, _, _ = _request(
                port, "POST", "/api/inventory/import.csv", import_body, cookie
            )
            self.assertEqual(status, 200)
        unchanged_status, _, _ = _request(
            first_port,
            "POST",
            "/api/inventory/import.csv",
            {**import_body, "idempotency_key": "ledger-import-unchanged"},
            cookie,
        )
        self.assertEqual(unchanged_status, 200)

        invalid_status, _, _ = _request(
            first_port,
            "POST",
            "/api/inventory/import.csv",
            {
                "csv": "id,type,count\nledger race item,other,7\nledger race item,other,8\n",
                "scope": "shared",
                "idempotency_key": "ledger-invalid-import",
            },
            cookie,
        )
        self.assertEqual(invalid_status, 400)

        tech_status, _, tech_headers = _request(
            first_port,
            "POST",
            "/api/auth/signin",
            {"email": "runtime-other@example.test", "password": "other-password"},
        )
        self.assertEqual(tech_status, 200)
        tech_cookie = tech_headers["set-cookie"].split(";", 1)[0]
        denied_status, _, _ = _request(
            second_port,
            "POST",
            "/api/inventory/items",
            {
                "id": "unauthorized shared item",
                "amount": 1,
                "scope": "shared",
                "idempotency_key": "unauthorized-shared-add",
            },
            tech_cookie,
        )
        self.assertEqual(denied_status, 403)

        with self.factory() as session:
            holding = session.scalar(
                select(InventoryHoldingModel).where(
                    InventoryHoldingModel.organization_id == "runtime-org",
                    InventoryHoldingModel.legacy_item_id == "ledger race item",
                )
            )
            adjustments = list(
                session.scalars(
                    select(InventoryAdjustmentModel)
                    .where(InventoryAdjustmentModel.holding_id == holding.id)
                    .order_by(InventoryAdjustmentModel.created_at)
                )
            )
            adjustment_audits = session.scalar(
                select(func.count()).select_from(AuditEventModel).where(
                    AuditEventModel.resource_id == holding.id,
                    AuditEventModel.action == "inventory.adjusted",
                )
            )
            invalid_request_count = session.scalar(
                select(func.count()).select_from(OperationRequestModel).where(
                    OperationRequestModel.idempotency_key == "ledger-invalid-import"
                )
            )
        self.assertEqual(holding.available_quantity, 5)
        self.assertEqual([adjustment.delta for adjustment in adjustments], [1, 1, 1, 2])
        self.assertEqual(adjustment_audits, 4)
        self.assertEqual(invalid_request_count, 0)
        _request(first_port, "POST", "/api/auth/signout", {}, cookie)
        _request(first_port, "POST", "/api/auth/signout", {}, tech_cookie)

    def test_disabled_user_and_membership_invalidate_existing_cookie(self):
        first_port = self.ports[0]
        cookie = self._sign_in(first_port)
        with self.factory.begin() as session:
            session.get(UserModel, "runtime-owner").status = "disabled"
        status, state, _ = _request(first_port, "GET", "/api/state", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertFalse(state["auth"]["authenticated"])
        protected_status, _, _ = _request(
            first_port,
            "POST",
            "/api/inventory/items",
            {"id": "blocked item", "amount": 1, "scope": "shared"},
            cookie,
        )
        self.assertEqual(protected_status, 401)
        with self.factory.begin() as session:
            session.get(UserModel, "runtime-owner").status = "active"
        _request(first_port, "POST", "/api/auth/signout", {}, cookie)

        membership_cookie = self._sign_in(first_port)
        with self.factory.begin() as session:
            session.get(MembershipModel, "runtime-membership").status = "disabled"
        status, state, _ = _request(
            first_port, "GET", "/api/state", cookie=membership_cookie
        )
        self.assertEqual(status, 200)
        self.assertFalse(state["auth"]["authenticated"])
        with self.factory.begin() as session:
            session.get(MembershipModel, "runtime-membership").status = "active"
        _request(first_port, "POST", "/api/auth/signout", {}, membership_cookie)

    def test_event_creation_ids_are_rejected_without_disclosure_or_mutation(self):
        port = self.ports[0]
        cookie = self._sign_in(port)
        suffix = uuid4().hex
        foreign_org_id = f"foreign-org-{suffix}"
        foreign_user_id = f"foreign-user-{suffix}"
        foreign_membership_id = f"foreign-membership-{suffix}"
        foreign_event_id = f"foreign-event-{suffix}"
        own_event_id = f"own-event-{suffix}"

        def event_row(event_id: str, organization_id: str, owner_id: str) -> EventModel:
            return EventModel(
                id=event_id,
                organization_id=organization_id,
                owner_user_id=owner_id,
                title="Creation ID boundary fixture",
                status="planning",
                data={
                    "id": event_id,
                    "organization_id": organization_id,
                    "owner_id": owner_id,
                    "title": "Creation ID boundary fixture",
                    "description": "Must remain unchanged.",
                    "status": "planning",
                    "plan": {"lines": []},
                    "history": [],
                    "movements": [],
                },
            )

        with self.factory.begin() as session:
            session.add(OrganizationModel(id=foreign_org_id, name="Foreign Organization"))
            session.add(
                UserModel(
                    id=foreign_user_id,
                    email=f"foreign-{suffix}@example.test",
                    name="Foreign Owner",
                    password_hash=AccountStore.hash_password("foreign-password"),
                )
            )
            session.flush()
            session.add(
                MembershipModel(
                    id=foreign_membership_id,
                    organization_id=foreign_org_id,
                    user_id=foreign_user_id,
                    role="owner",
                )
            )
            session.flush()
            session.add(event_row(foreign_event_id, foreign_org_id, foreign_user_id))
            session.add(event_row(own_event_id, "runtime-org", "runtime-owner"))

        mutation_tables = (
            "events",
            "inventory_holdings",
            "allocations",
            "allocation_lines",
            "stock_movements",
            "inventory_adjustments",
            "audit_events",
            "operation_requests",
        )

        def mutation_snapshot() -> dict[str, str]:
            with self.engine.connect() as connection:
                return {
                    table: connection.execute(
                        text(
                            f"SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY t.id), "
                            f"'[]'::jsonb)::text FROM {table} AS t"
                        )
                    ).scalar_one()
                    for table in mutation_tables
                }

        before = mutation_snapshot()
        attempts = (
            {"event": {"id": foreign_event_id}},
            {"event": {"id": f"does-not-exist-{suffix}"}},
            {"event": {"id": {"malformed": True}}},
            {"event": {"id": own_event_id}},
            {"event_id": foreign_event_id},
        )
        responses = [
            _request(port, "POST", "/api/events/save", body, cookie)[:2]
            for body in attempts
        ]

        self.assertTrue(all(response == responses[0] for response in responses))
        self.assertEqual(
            responses[0],
            (
                400,
                {
                    "error": (
                        "Client-selected event IDs are not accepted for event creation."
                    )
                },
            ),
        )
        self.assertEqual(mutation_snapshot(), before)

        status, payload, _ = _request(
            port,
            "POST",
            "/api/events/save",
            {
                "description": (
                    "A small internal meeting for 10 guests on 2026-09-24 "
                    "with no lighting."
                )
            },
            cookie,
        )
        self.assertEqual(status, 201, payload)
        created_id = payload["event"]["id"]
        self.assertNotIn(created_id, {foreign_event_id, own_event_id})
        with self.factory() as session:
            created = session.get(EventModel, created_id)
            self.assertIsNotNone(created)
            self.assertEqual(created.organization_id, "runtime-org")

    @race_required
    def test_durable_transactional_runtime_and_cross_process_dispatch(self):
        first_port, second_port = self.ports
        status, _, headers = _request(
            first_port,
            "POST",
            "/api/auth/signin",
            {
                "email": "runtime-owner@example.test",
                "password": "runtime-password",
            },
        )
        self.assertEqual(status, 200)
        cookie = headers["set-cookie"].split(";", 1)[0]

        status, state, _ = _request(second_port, "GET", "/api/state", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertTrue(state["auth"]["authenticated"])
        with self.factory() as session:
            active_sessions = session.scalar(
                select(func.count()).select_from(SessionModel).where(
                    SessionModel.revoked_at.is_(None)
                )
            )
            self.assertEqual(active_sessions, 1)

        with self.factory.begin() as session:
            session.add(
                InventoryHoldingModel(
                    id="other-private-holding",
                    organization_id="runtime-org",
                    legacy_item_id="private mic",
                    scope="personal",
                    owner_user_id="runtime-other",
                    available_quantity=1,
                )
            )
            session.add(
                EventModel(
                    id="foreign-personal-event",
                    organization_id="runtime-org",
                    owner_user_id="runtime-owner",
                    title="Foreign personal boundary",
                    status="planning",
                    data={
                        "plan_verified": True,
                        "plan": {
                            "lines": [
                                {"item_id": "private mic", "amount": 1, "missing": 0}
                            ]
                        },
                    },
                )
            )
        status, _, _ = _request(
            first_port,
            "POST",
            "/api/events/status",
            {"event_id": "foreign-personal-event", "status": "confirmed"},
            cookie,
        )
        self.assertEqual(status, 409)
        with self.factory() as session:
            private_holding = session.get(
                InventoryHoldingModel, "other-private-holding"
            )
            private_allocations = session.scalar(
                select(func.count()).select_from(AllocationModel).where(
                    AllocationModel.event_id == "foreign-personal-event"
                )
            )
            self.assertEqual(
                (private_holding.available_quantity, private_holding.reserved_quantity),
                (1, 0),
            )
            self.assertEqual(private_allocations, 0)

        status, _, _ = _request(
            first_port,
            "POST",
            "/api/inventory/items",
            {
                "id": "owner private mic",
                "type": "microphone",
                "amount": 1,
                "scope": "personal",
            },
            cookie,
        )
        self.assertEqual(status, 200)
        with self.factory.begin() as session:
            session.add(
                EventModel(
                    id="owner-personal-event-http",
                    organization_id="runtime-org",
                    owner_user_id="runtime-owner",
                    title="Owner personal inventory",
                    status="planning",
                    data={
                        "plan_verified": True,
                        "plan": {
                            "lines": [
                                {
                                    "item_id": "owner private mic",
                                    "amount": 1,
                                    "missing": 0,
                                }
                            ]
                        },
                    },
                )
            )
        status, _, _ = _request(
            second_port,
            "POST",
            "/api/events/status",
            {"event_id": "owner-personal-event-http", "status": "confirmed"},
            cookie,
        )
        self.assertEqual(status, 200)
        with self.factory() as session:
            owner_private = session.scalar(
                select(InventoryHoldingModel).where(
                    InventoryHoldingModel.organization_id == "runtime-org",
                    InventoryHoldingModel.owner_user_id == "runtime-owner",
                    InventoryHoldingModel.legacy_item_id == "owner private mic",
                )
            )
            self.assertEqual(
                (owner_private.available_quantity, owner_private.reserved_quantity),
                (0, 1),
            )

        status, _, _ = _request(
            first_port,
            "POST",
            "/api/inventory/items",
            {
                "id": "race mic",
                "type": "microphone",
                "amount": 2,
                "scope": "shared",
            },
            cookie,
        )
        self.assertEqual(status, 200)
        status, state, _ = _request(second_port, "GET", "/api/state", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(
            next(item for item in state["inventories"]["shared"]["items"] if item["id"] == "race mic")["count"],
            2,
        )

        status, created, _ = _request(
            first_port,
            "POST",
            "/api/events/save",
            {"description": "A small internal meeting for 10 guests with no lighting."},
            cookie,
        )
        self.assertEqual(status, 201)
        created_event_id = created["event"]["id"]
        with self.factory.begin() as session:
            self.assertIsNotNone(session.get(EventModel, created_event_id))
            session.add(
                EventModel(
                    id="runtime-race-event",
                    organization_id="runtime-org",
                    owner_user_id="runtime-owner",
                    title="Runtime race event",
                    status="planning",
                    data={
                        "id": "runtime-race-event",
                        "title": "Runtime race event",
                        "description": "Concurrent dispatch proof",
                        "start_date": "2026-09-20",
                        "start_time": "18:00",
                        "duration_minutes": 120,
                        "organization_id": "runtime-org",
                        "owner_id": "runtime-owner",
                        "status": "planning",
                        "plan_verified": True,
                        "plan": {
                            "lines": [
                                {"item_id": "race mic", "amount": 1, "missing": 0}
                            ]
                        },
                        "checklist": [],
                        "return_checklist": [],
                        "conflicts": [],
                        "history": [],
                    },
                )
            )

        for next_status in ("confirmed", "packed"):
            status, payload, _ = _request(
                first_port,
                "POST",
                "/api/events/status",
                {"event_id": "runtime-race-event", "status": next_status},
                cookie,
            )
            self.assertEqual(status, 200, payload)

        start = threading.Barrier(3)
        results: list[tuple[int, dict]] = []

        def dispatch(port: int):
            start.wait(timeout=10)
            status_code, payload, _ = _request(
                port,
                "POST",
                "/api/events/status",
                {"event_id": "runtime-race-event", "status": "out"},
                cookie,
            )
            results.append((status_code, payload))

        threads = [
            threading.Thread(target=dispatch, args=(first_port,)),
            threading.Thread(target=dispatch, args=(second_port,)),
        ]
        for thread in threads:
            thread.start()
        start.wait(timeout=10)
        for thread in threads:
            thread.join(timeout=15)

        self.assertEqual(sorted(code for code, _ in results), [200, 200])
        with self.factory() as session:
            event = session.get(EventModel, "runtime-race-event")
            holding = session.scalar(
                select(InventoryHoldingModel).where(
                    InventoryHoldingModel.organization_id == "runtime-org",
                    InventoryHoldingModel.legacy_item_id == "race mic",
                )
            )
            dispatch_count = session.scalar(
                select(func.count()).select_from(StockMovementModel).where(
                    StockMovementModel.organization_id == "runtime-org",
                    StockMovementModel.event_id == "runtime-race-event",
                    StockMovementModel.action == "out",
                )
            )
        self.assertEqual(event.status, "out")
        self.assertEqual(
            (
                holding.available_quantity,
                holding.reserved_quantity,
                holding.packed_quantity,
                holding.dispatched_quantity,
            ),
            (1, 0, 0, 1),
        )
        self.assertEqual(dispatch_count, 1)

        status, _, _ = _request(
            second_port,
            "POST",
            "/api/events/status",
            {"event_id": "runtime-race-event", "status": "returned"},
            cookie,
        )
        self.assertEqual(status, 200)
        with self.factory() as session:
            holding = session.scalar(
                select(InventoryHoldingModel).where(
                    InventoryHoldingModel.organization_id == "runtime-org",
                    InventoryHoldingModel.legacy_item_id == "race mic",
                )
            )
            self.assertEqual(
                (holding.available_quantity, holding.dispatched_quantity), (2, 0)
            )

        old_process, old_stop = self.processes[0]
        old_stop.set()
        old_process.join(timeout=10)
        restarted_port = self._start_process()
        self.ports[0] = restarted_port
        status, restarted_state, _ = _request(
            restarted_port, "GET", "/api/state", cookie=cookie
        )
        self.assertEqual(status, 200)
        self.assertTrue(restarted_state["auth"]["authenticated"])
        self.assertTrue(
            any(event["id"] == "runtime-race-event" for event in restarted_state["events"]["events"])
        )
        self.assertEqual(self._json_hashes(), self.json_before)
