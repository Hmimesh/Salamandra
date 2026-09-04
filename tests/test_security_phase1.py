from __future__ import annotations

import http.client
import json
import sys
import tempfile
import threading
import unittest
from contextlib import AbstractContextManager
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from accounts import AccountStore
from event_memory import EventMemory, EventRecord
from event_templates import Kit, TemplateCatalog
from integrations import IntegrationStore
from inventory_workspace import InventoryWorkspace
from Item_node import ItemNode, ItemType, Requirement
from item_classes import ItemClassCatalog
from server import SalamandraServer


class ServerHarness(AbstractContextManager):
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)

        class IsolatedServer(SalamandraServer):
            pass

        IsolatedServer.accounts = AccountStore(root / "users.json")
        IsolatedServer.workspace = InventoryWorkspace(root / "inventories.json")
        IsolatedServer.memory = EventMemory(root / "events.json")
        IsolatedServer.item_classes = ItemClassCatalog(root / "item_classes.json")
        IsolatedServer.integrations = IntegrationStore(root / "integrations.json")
        IsolatedServer.sessions = {}
        IsolatedServer.login_attempts = {}
        IsolatedServer.registration_attempts = {}
        IsolatedServer.event_create_requests = {}
        self.handler = IsolatedServer
        self.owner_a = IsolatedServer.accounts.create_user(
            name="Owner A",
            email="owner-a@example.test",
            password="owner-a-password",
            role="owner",
            organization_id="org-a",
            organization_name="Organization A",
        )
        self.tech_a = IsolatedServer.accounts.create_user(
            name="Tech A",
            email="tech-a@example.test",
            password="tech-a-password",
            role="technician",
            organization_id="org-a",
            organization_name="Organization A",
        )
        self.owner_b = IsolatedServer.accounts.create_user(
            name="Owner B",
            email="owner-b@example.test",
            password="owner-b-password",
            role="owner",
            organization_id="org-b",
            organization_name="Organization B",
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), IsolatedServer)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def __exit__(self, exc_type, exc_value, traceback):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temp.cleanup()

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        cookie: str = "",
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"} if payload is not None else {}
        if cookie:
            headers["Cookie"] = cookie
        headers.update(extra_headers or {})
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        response_headers = {name.lower(): value for name, value in response.getheaders()}
        connection.close()
        if raw and "application/json" in response_headers.get("content-type", ""):
            data = json.loads(raw.decode("utf-8"))
        else:
            data = {"raw": raw}
        return response.status, data, response_headers

    def sign_in(self, email: str, password: str) -> str:
        status, _, headers = self.request(
            "POST",
            "/api/auth/signin",
            {"email": email, "password": password},
        )
        if status != 200:
            raise AssertionError(f"Sign-in failed with HTTP {status}.")
        return headers["set-cookie"].split(";", 1)[0]


class TestSecurityPhaseOne(unittest.TestCase):
    def test_demo_sign_in_is_disabled_without_explicit_demo_mode(self):
        with ServerHarness() as app:
            status, _, _ = app.request("POST", "/api/auth/demo", {})

            self.assertEqual(status, 404)

    def test_repeated_failed_sign_in_is_rate_limited(self):
        with ServerHarness() as app:
            statuses = [
                app.request(
                    "POST",
                    "/api/auth/signin",
                    {"email": "owner-a@example.test", "password": "wrong-password"},
                )[0]
                for _ in range(6)
            ]

            self.assertEqual(statuses[:5], [401] * 5)
            self.assertEqual(statuses[5], 429)

    def test_technician_is_denied_every_administrative_or_shared_write_route(self):
        with ServerHarness() as app:
            cookie = app.sign_in("tech-a@example.test", "tech-a-password")
            post_cases = [
                ("/api/inventory/items", {"id": "speaker", "amount": 1, "scope": "shared"}),
                ("/api/inventory/items/update", {"id": "speaker", "amount": 1, "scope": "shared"}),
                ("/api/inventory/presets", {"preset_id": "sm58-mic", "scope": "shared"}),
                ("/api/inventory/use", {"item_id": "speaker", "amount": 1, "scope": "shared"}),
                ("/api/inventory/return", {"item_id": "speaker", "amount": 1, "scope": "shared"}),
                ("/api/inventory/remove", {"item_id": "speaker", "amount": 1, "scope": "shared"}),
                ("/api/inventory/save", {}),
                ("/api/inventory/import.csv", {"csv": "id,count\nspeaker,1", "scope": "shared"}),
                ("/api/item-classes", {"id": "class", "name": "Class"}),
                ("/api/item-classes/clone", {"source_id": "main-pa", "id": "class", "name": "Class"}),
                ("/api/item-classes/remove", {"class_id": "class"}),
                ("/api/events/plan", {"items": [{"item_id": "speaker", "amount": 1}]}),
                ("/api/events/template", {"source_type": "template", "source_id": "conference-basic"}),
                (
                    "/api/kits/checkout",
                    {"source_id": "speech-kit", "idempotency_key": "unauthorized-kit"},
                ),
                ("/api/events/describe", {"description": "Small event"}),
                ("/api/events/save", {"description": "Small event"}),
                ("/api/integrations/configure", {"integration_id": "crm", "endpoint": "https://example.com"}),
                ("/api/team/invite", {"name": "User", "email": "user@example.test", "password": "password"}),
                ("/api/sync/run", {}),
            ]
            for path, body in post_cases:
                with self.subTest(path=path):
                    status, _, _ = app.request("POST", path, body, cookie)
                    self.assertEqual(status, 403)

            for path in ("/api/templates", "/api/inventory/export.csv"):
                with self.subTest(path=path):
                    status, _, _ = app.request("GET", path, cookie=cookie)
                    self.assertEqual(status, 403)

    def test_technician_can_write_only_their_personal_inventory(self):
        with ServerHarness() as app:
            cookie = app.sign_in("tech-a@example.test", "tech-a-password")

            status, _, _ = app.request(
                "POST",
                "/api/inventory/items",
                {"id": "personal tester", "amount": 1, "scope": "personal"},
                cookie,
            )

            self.assertEqual(status, 200)
            self.assertIsNotNone(
                app.handler.workspace.inventory_for("personal", app.tech_a.id, "org-a").get_item(
                    "personal tester"
                )
            )

    def test_unsigned_state_does_not_expose_tenant_events(self):
        with ServerHarness() as app:
            app.handler.memory.add(
                EventRecord(
                    id="org-b-secret-event",
                    title="Organization B confidential event",
                    description="Private customer event",
                    start_date="2026-09-10",
                    organization_id="org-b",
                )
            )

            status, payload, _ = app.request("GET", "/api/state")

            self.assertEqual(status, 200)
            self.assertFalse(payload["auth"]["authenticated"])
            self.assertEqual(payload["events"]["events"], [])
            self.assertEqual(payload["sync"]["pending_events"], [])

    def test_unsigned_mutation_is_rejected(self):
        with ServerHarness() as app:
            status, _, _ = app.request(
                "POST",
                "/api/inventory/items",
                {"id": "unsigned item", "amount": 1, "scope": "shared"},
            )

            self.assertEqual(status, 401)

    def test_org_a_cannot_read_org_b_event(self):
        with ServerHarness() as app:
            app.handler.memory.add(
                EventRecord(
                    id="org-b-event",
                    title="B event",
                    description="Private",
                    start_date="2026-09-10",
                    organization_id="org-b",
                )
            )
            cookie = app.sign_in("owner-a@example.test", "owner-a-password")

            status, _, _ = app.request(
                "POST",
                "/api/events/checklist",
                {"event_id": "org-b-event", "phase": "pack", "item_id": "mic"},
                cookie,
            )

            self.assertIn(status, {403, 404})

            state_status, state, _ = app.request("GET", "/api/state", cookie=cookie)
            self.assertEqual(state_status, 200)
            self.assertNotIn(
                "org-b-event",
                {event["id"] for event in state["events"]["events"]},
            )

    def test_org_a_cannot_use_or_export_org_b_inventory(self):
        with ServerHarness() as app:
            inventory_b = app.handler.workspace.inventory_for(
                "shared", app.owner_b.id, "org-b"
            )
            inventory_b.add_item(ItemNode("org b secret console", ItemType.MIXER), amount=1)
            app.handler.workspace.save()
            cookie = app.sign_in("owner-a@example.test", "owner-a-password")

            use_status, _, _ = app.request(
                "POST",
                "/api/inventory/use",
                {"item_id": "org b secret console", "amount": 1, "scope": "shared"},
                cookie,
            )
            export_status, export, _ = app.request(
                "GET", "/api/inventory/export.csv", cookie=cookie
            )

            self.assertEqual(use_status, 404)
            self.assertEqual(export_status, 200)
            self.assertEqual(inventory_b.get_item("org b secret console").count, 1)
            self.assertNotIn("org b secret console", export["raw"].decode("utf-8-sig"))

    def test_org_a_cannot_overwrite_org_b_event(self):
        with ServerHarness() as app:
            victim = EventRecord(
                id="org-b-event",
                title="B event",
                description="Private",
                start_date="2026-09-10",
                organization_id="org-b",
            )
            app.handler.memory.add(victim)
            cookie = app.sign_in("owner-a@example.test", "owner-a-password")

            status, payload, _ = app.request(
                "POST",
                "/api/events/save",
                {
                    "event": {
                        **victim.to_dict(),
                        "title": "Overwritten by A",
                        "organization_id": "org-a",
                    }
                },
                cookie,
            )

            self.assertEqual(status, 400)
            self.assertEqual(
                payload,
                {
                    "error": (
                        "Client-selected event IDs are not accepted for event creation."
                    )
                },
            )
            stored = app.handler.memory.get("org-b-event")
            self.assertEqual(stored.organization_id, "org-b")
            self.assertEqual(stored.title, "B event")

    def test_lower_role_cannot_mutate_shared_inventory_directly(self):
        with ServerHarness() as app:
            cookie = app.sign_in("tech-a@example.test", "tech-a-password")

            status, _, _ = app.request(
                "POST",
                "/api/inventory/items",
                {"id": "unauthorized speaker", "amount": 1, "scope": "shared"},
                cookie,
            )

            self.assertEqual(status, 403)
            self.assertIsNone(
                app.handler.workspace.combined_inventory(app.tech_a.id, "org-a").get_item(
                    "unauthorized speaker"
                )
            )

    def test_forged_allocation_plan_is_not_persisted(self):
        with ServerHarness() as app:
            cookie = app.sign_in("owner-a@example.test", "owner-a-password")

            status, payload, _ = app.request(
                "POST",
                "/api/events/save",
                {
                    "description": "Small spoken presentation for 20 guests on 2026-09-10.",
                    "overrides": {"title": "Server planned event"},
                    "event": {
                        "title": "Forged event",
                        "description": "Ignored",
                        "start_date": "2026-09-10",
                        "organization_id": "org-b",
                        "plan": {
                            "lines": [
                                {"item_id": "nonexistent gold console", "amount": 99}
                            ]
                        },
                    },
                },
                cookie,
            )

            self.assertEqual(status, 201)
            self.assertEqual(payload["event"]["organization_id"], "org-a")
            self.assertNotEqual(payload["event"]["id"], "org-b-event")
            planned_ids = {
                line.get("item_id") for line in payload["event"]["plan"].get("lines", [])
            }
            self.assertNotIn("nonexistent gold console", planned_ids)

    def test_duplicate_event_creation_returns_the_original_event(self):
        with ServerHarness() as app:
            cookie = app.sign_in("owner-a@example.test", "owner-a-password")
            body = {
                "description": "Small speech for 20 guests on 2026-09-10 at 18:00.",
                "overrides": {"title": "Retry-safe event"},
                "idempotency_key": "create-event-retry-1",
            }

            first_status, first_payload, _ = app.request(
                "POST", "/api/events/save", body, cookie
            )
            second_status, second_payload, _ = app.request(
                "POST", "/api/events/save", body, cookie
            )

            self.assertEqual((first_status, second_status), (201, 200))
            self.assertEqual(first_payload["event"]["id"], second_payload["event"]["id"])
            self.assertEqual(len(app.handler.memory.list_events("org-a")), 1)

    def test_event_creation_key_reuse_with_different_payload_conflicts(self):
        with ServerHarness() as app:
            cookie = app.sign_in("owner-a@example.test", "owner-a-password")
            key = "create-event-conflict-1"
            first_status, _, _ = app.request(
                "POST",
                "/api/events/save",
                {
                    "description": "Small speech on 2026-09-10.",
                    "idempotency_key": key,
                },
                cookie,
            )
            second_status, payload, _ = app.request(
                "POST",
                "/api/events/save",
                {
                    "description": "Different concert on 2026-09-11.",
                    "idempotency_key": key,
                },
                cookie,
            )

            self.assertEqual(first_status, 201)
            self.assertEqual(second_status, 409)
            self.assertIn("another event", payload["error"])
            self.assertEqual(len(app.handler.memory.list_events("org-a")), 1)

    def test_concurrent_duplicate_event_creation_persists_once(self):
        with ServerHarness() as app:
            cookie = app.sign_in("owner-a@example.test", "owner-a-password")
            body = {
                "description": "Small speech for 20 guests on 2026-09-10 at 18:00.",
                "overrides": {"title": "Double-click event"},
                "idempotency_key": "create-event-race-1",
            }
            start = threading.Barrier(3)
            results: list[tuple[int, dict[str, Any]]] = []

            def create():
                start.wait(timeout=5)
                status, payload, _ = app.request(
                    "POST", "/api/events/save", body, cookie
                )
                results.append((status, payload))

            threads = [threading.Thread(target=create) for _ in range(2)]
            for thread in threads:
                thread.start()
            start.wait(timeout=5)
            for thread in threads:
                thread.join(timeout=5)

            self.assertEqual(sorted(status for status, _ in results), [200, 201])
            self.assertEqual(
                len({payload["event"]["id"] for _, payload in results}), 1
            )
            self.assertEqual(len(app.handler.memory.list_events("org-a")), 1)

    def test_planning_event_can_be_edited_and_stale_edit_is_rejected(self):
        with ServerHarness() as app:
            event = EventRecord(
                id="editable-event",
                title="Original title",
                description="Small event on 2026-09-10.",
                start_date="2026-09-10",
                organization_id="org-a",
                owner_id=app.owner_a.id,
            )
            app.handler.memory.add(event)
            cookie = app.sign_in("owner-a@example.test", "owner-a-password")
            edit = {
                "event_id": event.id,
                "version": 1,
                "description": "Small event for 45 guests on 2026-09-12 at 19:00.",
                "overrides": {
                    "title": "Updated title",
                    "start_date": "2026-09-12",
                    "start_time": "19:00",
                    "location": "New venue",
                    "attendee_count": 45,
                },
            }

            status, payload, _ = app.request(
                "POST", "/api/events/update", edit, cookie
            )
            stale_status, stale_payload, _ = app.request(
                "POST", "/api/events/update", edit, cookie
            )

            self.assertEqual(status, 200)
            self.assertEqual(payload["event"]["title"], "Updated title")
            self.assertEqual(payload["event"]["attendee_count"], 45)
            self.assertEqual(payload["event"]["version"], 2)
            self.assertEqual(stale_status, 409)
            self.assertIn("Reload", stale_payload["error"])

    def test_event_edit_is_scoped_authorized_and_lifecycle_restricted(self):
        with ServerHarness() as app:
            event_b = EventRecord(
                id="org-b-edit-event",
                title="Org B event",
                description="Private event on 2026-09-10.",
                start_date="2026-09-10",
                organization_id="org-b",
                owner_id=app.owner_b.id,
            )
            confirmed_a = EventRecord(
                id="confirmed-edit-event",
                title="Confirmed event",
                description="Confirmed event on 2026-09-10.",
                start_date="2026-09-10",
                organization_id="org-a",
                owner_id=app.owner_a.id,
                status="confirmed",
            )
            app.handler.memory.add(event_b)
            app.handler.memory.add(confirmed_a)
            owner_cookie = app.sign_in("owner-a@example.test", "owner-a-password")
            tech_cookie = app.sign_in("tech-a@example.test", "tech-a-password")
            edit = {
                "version": 1,
                "description": "Changed event on 2026-09-12.",
                "overrides": {"title": "Changed"},
            }

            cross_status, _, _ = app.request(
                "POST",
                "/api/events/update",
                {**edit, "event_id": event_b.id},
                owner_cookie,
            )
            restricted_status, restricted_payload, _ = app.request(
                "POST",
                "/api/events/update",
                {**edit, "event_id": confirmed_a.id},
                owner_cookie,
            )
            unauthorized_status, _, _ = app.request(
                "POST",
                "/api/events/update",
                {**edit, "event_id": confirmed_a.id},
                tech_cookie,
            )

            self.assertEqual(cross_status, 404)
            self.assertEqual(restricted_status, 409)
            self.assertIn("Only planning events", restricted_payload["error"])
            self.assertEqual(unauthorized_status, 403)
            self.assertEqual(app.handler.memory.get(event_b.id).title, "Org B event")

    def test_signed_in_workspace_does_not_claim_suggested_kits_or_templates(self):
        with ServerHarness() as app:
            cookie = app.sign_in("owner-a@example.test", "owner-a-password")

            status, payload, _ = app.request("GET", "/api/state", cookie=cookie)

            self.assertEqual(status, 200)
            self.assertEqual(payload["templates"]["kits"], [])
            self.assertEqual(payload["templates"]["templates"], [])
            self.assertGreater(len(payload["templates"]["suggested_kits"]), 0)
            self.assertGreater(len(payload["templates"]["suggested_templates"]), 0)

    def test_invalid_status_transition_is_rejected(self):
        with ServerHarness() as app:
            event = EventRecord(
                id="planning-event",
                title="Planning event",
                description="Draft",
                start_date="2026-09-10",
                organization_id="org-a",
                status="planning",
            )
            app.handler.memory.add(event)
            cookie = app.sign_in("owner-a@example.test", "owner-a-password")

            status, _, _ = app.request(
                "POST",
                "/api/events/status",
                {"event_id": event.id, "status": "out"},
                cookie,
            )

            self.assertEqual(status, 409)
            self.assertEqual(app.handler.memory.get(event.id).status, "planning")

    def test_duplicate_dispatch_moves_inventory_once(self):
        with ServerHarness() as app:
            inventory = app.handler.workspace.inventory_for("shared", app.owner_a.id, "org-a")
            inventory.add_item(ItemNode("dispatch mic", ItemType.MIC), amount=2)
            app.handler.workspace.save()
            event = EventRecord(
                id="packed-event",
                title="Packed event",
                description="Ready",
                start_date="2026-09-10",
                organization_id="org-a",
                status="packed",
                plan={"lines": [{"item_id": "dispatch mic", "amount": 1, "missing": 0}]},
            )
            app.handler.memory.add(event)
            cookie = app.sign_in("owner-a@example.test", "owner-a-password")

            first, _, _ = app.request(
                "POST", "/api/events/status", {"event_id": event.id, "status": "out"}, cookie
            )
            second, _, _ = app.request(
                "POST", "/api/events/status", {"event_id": event.id, "status": "out"}, cookie
            )

            item = inventory.get_item("dispatch mic")
            self.assertEqual((first, second), (200, 200))
            self.assertEqual((item.count, item.in_use_count), (1, 1))

    def test_concurrent_duplicate_dispatch_moves_inventory_once(self):
        with ServerHarness() as app:
            inventory = app.handler.workspace.inventory_for(
                "shared", app.owner_a.id, "org-a"
            )
            inventory.add_item(ItemNode("race dispatch mic", ItemType.MIC), amount=2)
            app.handler.workspace.save()
            event = EventRecord(
                id="concurrent-packed-event",
                title="Concurrent packed event",
                description="Ready",
                start_date="2026-09-10",
                organization_id="org-a",
                owner_id=app.owner_a.id,
                status="packed",
                plan={
                    "lines": [
                        {"item_id": "race dispatch mic", "amount": 1, "missing": 0}
                    ]
                },
            )
            app.handler.memory.add(event)
            cookie = app.sign_in("owner-a@example.test", "owner-a-password")
            start = threading.Barrier(3)
            mutation_race = threading.Barrier(2)
            results: list[tuple[int, dict[str, Any]]] = []
            original_apply = app.handler.workspace.apply_scope_allocations

            def apply_after_both_requests_validate(*args, **kwargs):
                try:
                    mutation_race.wait(timeout=0.5)
                except threading.BrokenBarrierError:
                    pass
                return original_apply(*args, **kwargs)

            app.handler.workspace.apply_scope_allocations = (
                apply_after_both_requests_validate
            )

            def dispatch():
                start.wait(timeout=5)
                status, payload, _ = app.request(
                    "POST",
                    "/api/events/status",
                    {"event_id": event.id, "status": "out"},
                    cookie,
                )
                results.append((status, payload))

            threads = [threading.Thread(target=dispatch) for _ in range(2)]
            for thread in threads:
                thread.start()
            start.wait(timeout=5)
            for thread in threads:
                thread.join(timeout=5)

            stored = app.handler.memory.get(event.id)
            item = inventory.get_item("race dispatch mic")
            dispatch_movements = [
                movement
                for movement in stored.movements
                if movement.get("action") == "dispatch"
            ]
            self.assertEqual(sorted(status for status, _ in results), [200, 200])
            self.assertEqual((item.count, item.in_use_count), (1, 1))
            self.assertEqual(stored.status, "out")
            self.assertEqual(len(dispatch_movements), 1)

    def test_duplicate_kit_checkout_creates_one_event_and_movement(self):
        with ServerHarness() as app:
            app.handler.templates = TemplateCatalog(
                kits=[
                    Kit(
                        id="test-kit",
                        name="Test Kit",
                        category="test",
                        description="One authoritative test item.",
                        items=(Requirement("kit checkout item", 1),),
                    )
                ]
            )
            inventory = app.handler.workspace.inventory_for(
                "shared", app.owner_a.id, "org-a"
            )
            inventory.add_item(
                ItemNode("kit checkout item", ItemType.OTHER), amount=2
            )
            app.handler.workspace.save()
            cookie = app.sign_in("owner-a@example.test", "owner-a-password")
            body = {
                "source_id": "test-kit",
                "idempotency_key": "checkout-request-1",
            }

            first_status, first_payload, _ = app.request(
                "POST", "/api/kits/checkout", body, cookie
            )
            second_status, second_payload, _ = app.request(
                "POST", "/api/kits/checkout", body, cookie
            )

            item = inventory.get_item("kit checkout item")
            events = app.handler.memory.list_events("org-a")
            dispatch_movements = [
                movement
                for event in events
                for movement in event.movements
                if movement.get("action") == "dispatch"
            ]
            self.assertEqual((first_status, second_status), (201, 200))
            self.assertEqual(first_payload["event"]["id"], second_payload["event"]["id"])
            self.assertEqual((item.count, item.in_use_count), (1, 1))
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].status, "out")
            self.assertEqual(events[0].source_id, "test-kit")
            self.assertEqual(len(dispatch_movements), 1)
            self.assertEqual(
                dispatch_movements[0]["idempotency_key"],
                "kit:checkout-request-1",
            )

    def test_legacy_client_plan_checkout_is_removed(self):
        with ServerHarness() as app:
            inventory = app.handler.workspace.inventory_for(
                "shared", app.owner_a.id, "org-a"
            )
            inventory.add_item(ItemNode("client supplied item", ItemType.OTHER), amount=2)
            app.handler.workspace.save()
            cookie = app.sign_in("owner-a@example.test", "owner-a-password")

            status, _, _ = app.request(
                "POST",
                "/api/events/use",
                {"items": [{"item_id": "client supplied item", "amount": 1}]},
                cookie,
            )

            item = inventory.get_item("client supplied item")
            self.assertEqual(status, 404)
            self.assertEqual((item.count, item.in_use_count), (2, 0))
            self.assertEqual(app.handler.memory.list_events("org-a"), [])

    def test_duplicate_return_restores_inventory_once(self):
        with ServerHarness() as app:
            inventory = app.handler.workspace.inventory_for("shared", app.owner_a.id, "org-a")
            item = ItemNode("return mic", ItemType.MIC, count=2)
            inventory.items[item.id] = item
            app.handler.workspace.save()
            event = EventRecord(
                id="out-event",
                title="Out event",
                description="Ready to dispatch",
                start_date="2026-09-10",
                organization_id="org-a",
                owner_id=app.owner_a.id,
                status="packed",
                plan={"lines": [{"item_id": "return mic", "amount": 1, "missing": 0}]},
                return_checklist=[
                    {"id": "return-0", "item_id": "return mic", "amount": 1, "done": True}
                ],
            )
            app.handler.memory.add(event)
            cookie = app.sign_in("owner-a@example.test", "owner-a-password")

            dispatched, _, _ = app.request(
                "POST",
                "/api/events/status",
                {"event_id": event.id, "status": "out"},
                cookie,
            )

            first, _, _ = app.request(
                "POST",
                "/api/events/status",
                {"event_id": event.id, "status": "returned"},
                cookie,
            )
            second, _, _ = app.request(
                "POST",
                "/api/events/status",
                {"event_id": event.id, "status": "returned"},
                cookie,
            )

            self.assertEqual((dispatched, first, second), (200, 200, 200))
            self.assertEqual((item.count, item.in_use_count), (2, 0))

    def test_malformed_event_id_returns_validation_error(self):
        with ServerHarness() as app:
            cookie = app.sign_in("owner-a@example.test", "owner-a-password")

            status, _, _ = app.request(
                "POST",
                "/api/events/status",
                {"event_id": {"not": "a string"}, "status": "confirmed"},
                cookie,
            )

            self.assertEqual(status, 400)

    def test_nonexistent_inventory_id_does_not_change_stock(self):
        with ServerHarness() as app:
            cookie = app.sign_in("owner-a@example.test", "owner-a-password")

            status, _, _ = app.request(
                "POST",
                "/api/inventory/use",
                {"item_id": "does not exist", "amount": 1, "scope": "shared"},
                cookie,
            )

            self.assertEqual(status, 404)
            self.assertEqual(
                app.handler.workspace.combined_inventory(app.owner_a.id, "org-a").summary()[
                    "in_use"
                ],
                0,
            )

    def test_csv_import_is_atomic_when_a_later_row_is_invalid(self):
        with ServerHarness() as app:
            cookie = app.sign_in("owner-a@example.test", "owner-a-password")

            status, _, _ = app.request(
                "POST",
                "/api/inventory/import.csv",
                {
                    "scope": "shared",
                    "csv": "id,count\nvalid first row,2\ninvalid second row,not-a-number",
                },
                cookie,
            )

            self.assertEqual(status, 400)
            inventory = app.handler.workspace.inventory_for("shared", app.owner_a.id, "org-a")
            self.assertIsNone(inventory.get_item("valid first row"))

    def test_csv_export_neutralizes_spreadsheet_formulas(self):
        with ServerHarness() as app:
            inventory = app.handler.workspace.inventory_for("shared", app.owner_a.id, "org-a")
            inventory.add_item(
                ItemNode("formula item", ItemType.OTHER, info="=HYPERLINK(\"https://evil.test\")"),
                amount=1,
            )
            app.handler.workspace.save()
            cookie = app.sign_in("owner-a@example.test", "owner-a-password")

            status, payload, _ = app.request(
                "GET", "/api/inventory/export.csv", cookie=cookie
            )

            self.assertEqual(status, 200)
            exported = payload["raw"].decode("utf-8-sig")
            self.assertIn("'=HYPERLINK", exported)

    def test_security_headers_are_sent(self):
        with ServerHarness() as app:
            status, _, headers = app.request("GET", "/api/state")

            self.assertEqual(status, 200)
            self.assertEqual(headers["x-content-type-options"], "nosniff")
            self.assertEqual(headers["x-frame-options"], "DENY")
            self.assertEqual(headers["cache-control"], "no-store")
            self.assertIn("frame-ancestors 'none'", headers["content-security-policy"])

    def test_frontend_does_not_ship_known_demo_credentials(self):
        shipped_files = [
            *list((ROOT / "frontend" / "src").rglob("*.ts")),
            *list((ROOT / "frontend" / "src").rglob("*.tsx")),
            *list((ROOT / "web" / "assets" / "app").glob("*.js")),
        ]
        shipped_text = "\n".join(
            path.read_text(encoding="utf-8") for path in shipped_files
        ).lower()

        for exposed_value in (
            "admin123",
            "ops123",
            "demo123",
            "welcome123",
            "maya@northstarlive.demo",
        ):
            self.assertNotIn(exposed_value, shipped_text)

    def test_cross_origin_mutation_is_rejected(self):
        with ServerHarness() as app:
            cookie = app.sign_in("owner-a@example.test", "owner-a-password")

            status, _, _ = app.request(
                "POST",
                "/api/account/preferences",
                {"theme": "dark"},
                cookie,
                {"Origin": "https://attacker.example"},
            )

            self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
