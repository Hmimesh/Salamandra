import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from Inventory import Inventory
from accounts import AccountStore
from event_planner import EventPlanner
from event_templates import TemplateCatalog
from event_intake import EventDescriptionPlanner
from event_memory import EventMemory, EventRecord
from inventory_workspace import InventoryWorkspace
from Item_node import ItemNode, ItemType, Requirement
from item_classes import (
    CapabilityRequirement,
    ConfiguredItemClass,
    DependencyRule,
    ItemClassCatalog,
)
from integrations import IntegrationStore
from presets import PresetCatalog


class TestInventory(unittest.TestCase):
    def setUp(self):
        self.inventory = Inventory()

    def test_add_item_stores_new_item_with_lowercase_id(self):
        item = ItemNode("Mixer-01", ItemType.MIXER, 0, [])

        self.inventory.add_item(item)

        stored_item = self.inventory.get_item("mixer-01")
        self.assertIsNotNone(stored_item)
        self.assertEqual(stored_item.id, "mixer-01")
        self.assertEqual(stored_item.count, 1)

    def test_add_existing_item_increases_stock(self):
        self.inventory.add_item(ItemNode("Cable", ItemType.CABLE))
        self.inventory.add_item(ItemNode(" cable ", ItemType.CABLE), amount=4)

        stored_item = self.inventory.get_item("CABLE")
        self.assertEqual(stored_item.count, 5)

    def test_add_item_uses_initial_count_when_amount_is_not_provided(self):
        self.inventory.add_item(ItemNode("Stand", ItemType.STAND, count=3))

        stored_item = self.inventory.get_item("stand")
        self.assertEqual(stored_item.count, 3)

    def test_remove_item_deletes_item_when_stock_reaches_zero(self):
        self.inventory.add_item(ItemNode("Cable", ItemType.CABLE))

        was_removed = self.inventory.remove_item("cable")

        self.assertTrue(was_removed)
        self.assertIsNone(self.inventory.get_item("cable"))

    def test_remove_missing_item_returns_false(self):
        self.assertFalse(self.inventory.remove_item("missing"))

    def test_use_item_updates_stock_and_in_use_count(self):
        self.inventory.add_item(ItemNode("Mic", ItemType.MIC))

        was_used = self.inventory.use_item("mic")

        stored_item = self.inventory.get_item("mic")
        self.assertTrue(was_used)
        self.assertEqual(stored_item.count, 0)
        self.assertEqual(stored_item.in_use_count, 1)

    def test_return_item_moves_count_back_to_stock(self):
        self.inventory.add_item(ItemNode("Mic", ItemType.MIC), amount=2)
        self.inventory.use_item("mic")

        was_returned = self.inventory.return_item("mic")

        stored_item = self.inventory.get_item("mic")
        self.assertTrue(was_returned)
        self.assertEqual(stored_item.count, 2)
        self.assertEqual(stored_item.in_use_count, 0)

    def test_use_item_fails_when_stock_is_not_available(self):
        self.inventory.add_item(ItemNode("Mic", ItemType.MIC))

        with self.assertRaises(ValueError):
            self.inventory.use_item("mic", amount=2)

    def test_requirement_check_reports_missing_items(self):
        mixer = ItemNode(
            "Mixer",
            ItemType.MIXER,
            req=[Requirement("Cable", 2), Requirement("Stand", 1)],
        )
        self.inventory.add_item(mixer)
        self.inventory.add_item(ItemNode("Cable", ItemType.CABLE))

        can_use, missing = self.inventory.can_use_requirements("mixer")

        self.assertFalse(can_use)
        self.assertEqual(
            missing,
            [
                "cable: needs 2, available 1",
                "stand: needs 1, available 0",
            ],
        )

    def test_requirement_check_passes_when_stock_is_available(self):
        mixer = ItemNode("Mixer", ItemType.MIXER, req=[Requirement("Cable", 2)])
        self.inventory.add_item(mixer)
        self.inventory.add_item(ItemNode("Cable", ItemType.CABLE), amount=2)

        can_use, missing = self.inventory.can_use_requirements("mixer")

        self.assertTrue(can_use)
        self.assertEqual(missing, [])

    def test_add_from_preset_adds_default_item(self):
        catalog = PresetCatalog()
        preset = catalog.get("sm58-mic")

        stored_item = self.inventory.add_from_preset(preset)

        self.assertEqual(stored_item.id, "shure sm58 microphone")
        self.assertEqual(stored_item.type, ItemType.MIC)
        self.assertEqual(stored_item.count, 1)

    def test_add_from_preset_can_override_id_and_amount(self):
        catalog = PresetCatalog()
        preset = catalog.get("xlr-10m")

        stored_item = self.inventory.add_from_preset(preset, item_id="XLR Rack A", amount=6)

        self.assertEqual(stored_item.id, "xlr rack a")
        self.assertEqual(stored_item.type, ItemType.CABLE)
        self.assertEqual(stored_item.count, 6)

    def test_inventory_summary_counts_backend_state(self):
        self.inventory.add_item(ItemNode("Mic", ItemType.MIC), amount=3)
        self.inventory.add_item(ItemNode("Mixer", ItemType.MIXER, req=[Requirement("Cable", 2)]))
        self.inventory.use_item("mic", amount=2)

        self.assertEqual(
            self.inventory.summary(),
            {
                "unique_items": 2,
                "in_stock": 2,
                "in_use": 2,
                "requirements": 1,
            },
        )

    def test_inventory_round_trips_through_json_file(self):
        self.inventory.add_item(
            ItemNode("Mixer", ItemType.MIXER, req=[Requirement("Cable", 2)])
        )
        self.inventory.add_item(ItemNode("Cable", ItemType.CABLE), amount=3)
        self.inventory.use_item("cable")

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "inventory.json"

            self.inventory.save_inventory(path)
            loaded_inventory = Inventory.load_inventory(path)

        mixer = loaded_inventory.get_item("mixer")
        cable = loaded_inventory.get_item("cable")
        self.assertEqual(mixer.type, ItemType.MIXER)
        self.assertEqual(mixer.req, [Requirement("cable", 2)])
        self.assertEqual(cable.count, 2)
        self.assertEqual(cable.in_use_count, 1)


class TestEventPlanner(unittest.TestCase):
    def setUp(self):
        self.inventory = Inventory()
        self.catalog = PresetCatalog()
        self.planner = EventPlanner(self.inventory, self.catalog)

    def test_plan_expands_preset_requirements(self):
        plan = self.planner.build_plan([Requirement("Small Mixer", 1)])

        self.assertEqual(
            [(line.item_id, line.amount) for line in plan.lines],
            [
                ("small mixer", 1),
                ("xlr cable 10m", 2),
            ],
        )

    def test_plan_reports_available_and_missing_stock(self):
        self.inventory.add_from_preset(self.catalog.get("xlr-10m"), amount=1)

        plan = self.planner.build_plan([Requirement("Small Mixer", 1)])

        cable_line = next(line for line in plan.lines if line.item_id == "xlr cable 10m")
        self.assertEqual(cable_line.available, 1)
        self.assertEqual(cable_line.missing, 1)
        self.assertFalse(plan.is_ready)

    def test_plan_provisions_missing_items_from_presets(self):
        plan = self.planner.build_plan([Requirement("Small Mixer", 1)])

        added_lines = self.planner.provision_missing_from_presets(plan)
        refreshed_plan = self.planner.build_plan([Requirement("Small Mixer", 1)])

        self.assertEqual(
            sorted((line.item_id, line.missing) for line in added_lines),
            [
                ("small mixer", 1),
                ("xlr cable 10m", 2),
            ],
        )
        self.assertTrue(refreshed_plan.is_ready)

    def test_use_plan_marks_all_planned_items_in_use(self):
        plan = self.planner.build_plan([Requirement("Small Mixer", 1)])
        self.planner.provision_missing_from_presets(plan)
        ready_plan = self.planner.build_plan([Requirement("Small Mixer", 1)])

        self.planner.use_plan(ready_plan)

        mixer = self.inventory.get_item("small mixer")
        cable = self.inventory.get_item("xlr cable 10m")
        self.assertEqual(mixer.count, 0)
        self.assertEqual(mixer.in_use_count, 1)
        self.assertEqual(cable.count, 0)
        self.assertEqual(cable.in_use_count, 2)

    def test_use_plan_rejects_missing_stock(self):
        plan = self.planner.build_plan([Requirement("Small Mixer", 1)])

        with self.assertRaises(ValueError):
            self.planner.use_plan(plan)

    def test_plan_reports_conflicts_from_active_reservations(self):
        self.inventory.add_from_preset(self.catalog.get("pa-speaker"), amount=2)
        planner = EventPlanner(
            self.inventory,
            self.catalog,
            reserved_counts={"pa speaker": 1},
        )

        plan = planner.build_plan([Requirement("PA Speaker", 2)])

        speaker_line = next(line for line in plan.lines if line.item_id == "pa speaker")
        self.assertEqual(speaker_line.reserved_elsewhere, 1)
        self.assertEqual(speaker_line.missing, 1)
        self.assertTrue(speaker_line.conflict)


class TestEventDescriptionPlanner(unittest.TestCase):
    def setUp(self):
        self.inventory = Inventory()
        self.catalog = PresetCatalog()
        self.class_catalog = ItemClassCatalog()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.memory = EventMemory(Path(self.temp_dir.name) / "events.json")
        self.description_planner = EventDescriptionPlanner(
            self.inventory,
            self.catalog,
            self.memory,
            item_classes=self.class_catalog,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_manual_event_uses_structured_capabilities_without_text_inference(self):
        draft = self.description_planner.draft_from_description(
            "Manual community dinner.",
            overrides={
                "planning_mode": "manual",
                "start_date": "2026-09-21",
                "start_time": "18:00",
                "capability_requirements": [
                    {"capability": "furniture.table", "amount": 8, "level": "required"},
                    {"capability": "hospitality.service", "amount": 2, "level": "recommended"},
                ],
                "assigned_user_ids": ["owner", "operator"],
            },
        )
        self.assertEqual(
            [(need.capability, need.amount, need.level) for need in draft.record.capability_requirements],
            [
                ("furniture.table", 8, "required"),
                ("hospitality.service", 2, "recommended"),
            ],
        )
        self.assertEqual(draft.record.assigned_user_ids, ["owner", "operator"])
        self.assertNotIn("pa.main", {need.capability for need in draft.record.capability_requirements})

    def test_description_generates_plan_and_google_payload(self):
        draft = self.description_planner.draft_from_description(
            "Outdoor conference for 120 people on 2026-09-12 at 18:00, "
            "presentation, stage lighting and power at Main Hall."
        )

        requested_item_ids = {
            requirement.item_id for requirement in draft.record.requested_items
        }
        self.assertIn("pa speaker", requested_item_ids)
        self.assertIn("led par light", requested_item_ids)
        self.assertIn("power distro 63a", requested_item_ids)
        self.assertEqual(draft.record.start_date, "2026-09-12")
        self.assertEqual(draft.record.start_time, "18:00")
        self.assertEqual(draft.record.location, "Main Hall")
        self.assertEqual(
            draft.record.google_calendar_payload["timezone_str"],
            "Asia/Jerusalem",
        )

    def test_event_memory_suggests_items_from_similar_saved_events(self):
        first_draft = self.description_planner.draft_from_description(
            "VIP investor dinner on 2026-09-14 at 19:00 with speeches"
        )
        self.memory.add(first_draft.record)

        next_draft = self.description_planner.draft_from_description(
            "VIP investor dinner for board guests on 2026-10-01 at 20:00"
        )

        learned_item_ids = {
            requirement.item_id for requirement in next_draft.learned_items
        }
        self.assertTrue(learned_item_ids)

    def test_coffee_house_brief_parses_schedule_exclusions_and_audio_needs(self):
        draft = self.description_planner.draft_from_description(
            """Small performance: a singer who also uses acoustic guitar,
            saxophone, oud, and a harmonica player. The place is rather small
            in a coffee house, around 50 guests. 22.08
            15:30 - settling
            17:00 - balance
            19:00 - doors open
            20:00 - the show starts
            no lighting, no lights needed."""
        )

        requirements = {
            requirement.capability: requirement.amount
            for requirement in draft.record.capability_requirements
            if requirement.level == "required"
        }
        milestones = {
            milestone["label"]: milestone["time"]
            for milestone in draft.record.milestones
        }

        self.assertEqual(draft.record.start_date, "2026-08-22")
        self.assertEqual(draft.record.start_time, "15:30")
        self.assertEqual(draft.record.attendee_count, 50)
        self.assertEqual(draft.record.venue_kind, "coffee_house")
        self.assertEqual(draft.record.event_size, "small")
        self.assertEqual(milestones["Settling"], "15:30")
        self.assertEqual(milestones["Balance"], "17:00")
        self.assertEqual(milestones["Doors open"], "19:00")
        self.assertEqual(milestones["Show starts"], "20:00")
        self.assertEqual(requirements["pa.main"], 2)
        self.assertEqual(requirements["monitor.stage"], 2)
        self.assertEqual(requirements["microphone.vocal"], 1)
        self.assertEqual(requirements["microphone.instrument"], 3)
        self.assertEqual(requirements["di.instrument"], 1)
        self.assertNotIn("lighting.fixture", requirements)

    def test_coffee_house_plan_requires_xlr_for_every_pa_monitor_mic_and_di(self):
        for preset_id, amount in (
            ("ev-zlx-15p", 2),
            ("stage-monitor", 2),
            ("sm58-mic", 4),
            ("di-box", 1),
            ("small-mixer", 1),
            ("mic-stand", 4),
            ("xlr-10m", 12),
            ("power-cable", 6),
            ("standard-estate-car", 1),
            ("utility-cart", 1),
        ):
            self.inventory.add_from_preset(self.catalog.get(preset_id), amount=amount)

        draft = self.description_planner.draft_from_description(
            "Small performance with a singer on acoustic guitar, saxophone, oud, "
            "and harmonica in a coffee house for 50 guests on 22.08. "
            "15:30 settling, 17:00 balance, 19:00 doors open, 20:00 show. No lights."
        )
        xlr_lines = [
            line for line in draft.record.plan["lines"]
            if line.get("item_id") == "xlr cable 10m"
        ]
        microphone_lines = [
            line for line in draft.record.plan["lines"]
            if line.get("item_id") == "shure sm58 microphone"
        ]

        self.assertEqual(len(xlr_lines), 1)
        self.assertEqual(xlr_lines[0]["amount"], 11)
        self.assertEqual(xlr_lines[0]["required_amount"], 9)
        self.assertEqual(xlr_lines[0]["recommended_amount"], 2)
        self.assertEqual(len(microphone_lines), 1)
        self.assertEqual(microphone_lines[0]["amount"], 4)
        self.assertEqual(
            {component["capability"] for component in microphone_lines[0]["components"]},
            {"microphone.vocal", "microphone.instrument"},
        )
        self.assertFalse(any(line.get("type") == "lighting" for line in draft.record.plan["lines"]))
        self.assertTrue(draft.record.plan["is_ready"])

    def test_general_operations_items_are_allocated_from_plain_language(self):
        for preset_id, amount in (
            ("folding-chair", 20),
            ("folding-table", 4),
            ("crowd-barrier", 6),
            ("pop-up-canopy", 2),
            ("cargo-van", 1),
            ("utility-cart", 2),
        ):
            self.inventory.add_from_preset(self.catalog.get(preset_id), amount=amount)

        draft = self.description_planner.draft_from_description(
            "Outdoor community registration event for 180 guests on 2026-09-21 "
            "with 20 chairs, 4 tables, 6 barriers and 2 canopies. No lights."
        )
        allocated = {
            line["item_id"]: line["amount"]
            for line in draft.record.plan["lines"]
            if line.get("item_id")
        }

        self.assertEqual(allocated["folding chair"], 20)
        self.assertEqual(allocated["folding table"], 4)
        self.assertEqual(allocated["crowd barrier"], 6)
        self.assertEqual(allocated["pop-up canopy"], 2)

    def test_transport_plan_uses_payload_and_event_scale(self):
        for preset_id, amount in (
            ("ev-zlx-15p", 2),
            ("standard-estate-car", 1),
            ("utility-cart", 1),
        ):
            self.inventory.add_from_preset(self.catalog.get(preset_id), amount=amount)

        plan = EventPlanner(
            self.inventory,
            self.catalog,
            item_classes=self.class_catalog,
        ).build_capability_plan(
            [CapabilityRequirement("pa.main", 2)],
            {"event_size": "small", "title": "Coffee house"},
        )

        self.assertGreaterEqual(plan.transport_summary["payload_kg"], 34)
        self.assertEqual(plan.transport_summary["vehicle_capability"], "transport.vehicle.standard")
        self.assertEqual(plan.transport_summary["cart_count"], 1)
        self.assertTrue(any(line.item_id == "standard estate car" for line in plan.lines))
        self.assertTrue(any(line.item_id == "utility cart" for line in plan.lines))

    def test_medium_outdoor_and_large_festival_scale_requirements(self):
        medium = self.description_planner.draft_from_description(
            "Medium outdoor live event for 300 guests on 2026-09-18 at 16:00."
        )
        festival = self.description_planner.draft_from_description(
            "Large outdoor music festival for 2000 guests on 2026-09-18 at 19:00."
        )
        medium_needs = {
            item.capability: item.amount
            for item in medium.record.capability_requirements
        }
        festival_needs = {
            item.capability: item.amount
            for item in festival.record.capability_requirements
        }

        self.assertEqual(medium.record.event_size, "medium")
        self.assertEqual(medium_needs["pa.main"], 2)
        self.assertEqual(festival.record.event_size, "festival")
        self.assertGreaterEqual(festival_needs["pa.main"], 4)
        self.assertGreater(festival.record.priority_score, medium.record.priority_score)

    def test_custom_item_class_alias_is_understood_from_plain_language(self):
        self.class_catalog.upsert(
            ConfiguredItemClass(
                id="wireless-comms",
                name="Wireless comms beltpack",
                family="communication",
                capabilities=("comms.beltpack",),
                aliases=("comms pack",),
            ),
            "salamandra",
        )

        draft = self.description_planner.draft_from_description(
            "Small house event on 2026-09-22 at 18:00 with 6 comms packs."
        )
        requirements = {
            requirement.capability: requirement.amount
            for requirement in draft.record.capability_requirements
        }

        self.assertEqual(requirements["comms.beltpack"], 6)


class TestWeightedEventAllocation(unittest.TestCase):
    def setUp(self):
        self.inventory = Inventory()
        self.catalog = PresetCatalog()
        self.item_classes = ItemClassCatalog()
        self.inventory.add_from_preset(self.catalog.get("ev-zlx-15p"), amount=2)
        self.inventory.add_from_preset(self.catalog.get("pro-tech-15"), amount=4)
        self.inventory.add_from_preset(self.catalog.get("xlr-10m"), amount=12)
        self.inventory.add_from_preset(self.catalog.get("power-cable"), amount=12)
        self.inventory.add_from_preset(self.catalog.get("standard-estate-car"), amount=1)
        self.inventory.add_from_preset(self.catalog.get("cargo-van"), amount=1)
        self.inventory.add_from_preset(self.catalog.get("utility-cart"), amount=4)

    def test_same_day_festival_gets_better_pa_and_medium_gets_sufficient_substitute(self):
        planner = EventPlanner(
            self.inventory,
            self.catalog,
            item_classes=self.item_classes,
        )
        plans = planner.allocate_overlapping_events(
            [
                {
                    "id": "medium",
                    "title": "Outdoor community show",
                    "priority_score": 50,
                    "event_size": "medium",
                    "start_date": "2026-09-18",
                    "start_time": "16:00",
                    "duration_minutes": 420,
                    "capability_requirements": [
                        CapabilityRequirement("pa.main", 2).to_dict()
                    ],
                },
                {
                    "id": "festival",
                    "title": "September festival",
                    "priority_score": 90,
                    "event_size": "festival",
                    "start_date": "2026-09-18",
                    "start_time": "19:00",
                    "duration_minutes": 360,
                    "capability_requirements": [
                        CapabilityRequirement("pa.main", 4).to_dict()
                    ],
                },
            ]
        )

        festival_pa = {
            line.item_id: line.amount
            for line in plans["festival"].lines
            if line.capability == "pa.main" and line.item_id
        }
        medium_pa = {
            line.item_id: line.amount
            for line in plans["medium"].lines
            if line.capability == "pa.main" and line.item_id
        }

        self.assertEqual(festival_pa["ev zlx-15p"], 2)
        self.assertEqual(festival_pa["pro tech 15"], 2)
        self.assertEqual(medium_pa, {"pro tech 15": 2})
        self.assertTrue(plans["festival"].is_ready)
        self.assertTrue(plans["medium"].is_ready)

    def test_weighted_allocation_is_independent_of_event_entry_order(self):
        planner = EventPlanner(
            self.inventory,
            self.catalog,
            item_classes=self.item_classes,
        )
        medium = {
            "id": "medium",
            "title": "Medium event",
            "priority_score": 50,
            "event_size": "medium",
            "capability_requirements": [CapabilityRequirement("pa.main", 2).to_dict()],
        }
        festival = {
            "id": "festival",
            "title": "Festival",
            "priority_score": 90,
            "event_size": "festival",
            "capability_requirements": [CapabilityRequirement("pa.main", 4).to_dict()],
        }

        first = planner.allocate_overlapping_events([medium, festival])
        second = planner.allocate_overlapping_events([festival, medium])

        self.assertEqual(first["medium"].to_dict(), second["medium"].to_dict())
        self.assertEqual(first["festival"].to_dict(), second["festival"].to_dict())

    def test_packed_event_keeps_its_physical_allocation(self):
        planner = EventPlanner(
            self.inventory,
            self.catalog,
            item_classes=self.item_classes,
        )
        packed = {
            "id": "packed-show",
            "title": "Packed medium show",
            "status": "packed",
            "priority_score": 40,
            "capability_requirements": [CapabilityRequirement("pa.main", 2).to_dict()],
            "plan": {
                "lines": [
                    {
                        "item_id": "ev zlx-15p",
                        "amount": 2,
                        "available": 2,
                        "missing": 0,
                        "source": "event",
                        "type": "pa",
                        "capability": "pa.main",
                        "level": "required",
                    }
                ]
            },
        }
        festival = {
            "id": "festival",
            "title": "Late festival",
            "priority_score": 100,
            "capability_requirements": [CapabilityRequirement("pa.main", 4).to_dict()],
        }

        plans = planner.allocate_overlapping_events([festival, packed])
        packed_items = {
            line.item_id: line.amount for line in plans["packed-show"].lines
        }
        festival_items = {
            line.item_id: line.amount
            for line in plans["festival"].lines
            if line.capability == "pa.main" and line.item_id
        }

        self.assertEqual(packed_items, {"ev zlx-15p": 2})
        self.assertNotIn("ev zlx-15p", festival_items)


class TestAccountsAndWorkspace(unittest.TestCase):
    def test_default_account_store_does_not_seed_known_credentials(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AccountStore(Path(temp_dir) / "users.json")

        self.assertEqual(store.list_users(), [])
        self.assertIsNone(store.authenticate("admin@salamandra.local", "admin123"))

    def test_new_passwords_use_argon2(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AccountStore(Path(temp_dir) / "users.json")
            user = store.create_user(
                name="Secure User",
                email="secure@example.test",
                password="correct horse battery staple",
                role="operator",
                organization_id="northstar-live",
                organization_name="Northstar Live",
            )

        self.assertTrue(user.password_hash.startswith("$argon2"))
        self.assertTrue(store.verify_password("correct horse battery staple", user.password_hash))

    def test_seeded_demo_accounts_are_not_password_authenticatable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AccountStore(
                Path(temp_dir) / "users.json",
                seed_defaults=True,
                allow_demo=True,
            )

            user = store.get("admin")
            signed_in = store.authenticate("admin@salamandra.local", "admin123")

        self.assertIsNotNone(user)
        self.assertEqual(user.role, "admin")
        self.assertIsNone(signed_in)

    def test_workspace_combines_shared_and_personal_inventory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = InventoryWorkspace(Path(temp_dir) / "inventories.json")
            workspace.add_item(ItemNode("PA Speaker", ItemType.PA), 2, "shared", "admin")
            workspace.add_item(ItemNode("PA Speaker", ItemType.PA), 1, "personal", "admin")

            combined = workspace.combined_inventory("admin")

        self.assertEqual(combined.get_item("pa speaker").count, 3)

    def test_demo_company_accounts_are_seeded_and_scoped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AccountStore(
                Path(temp_dir) / "users.json",
                seed_defaults=True,
                allow_demo=True,
            )

            demo_user = store.get("demo-owner")
            demo_team = store.list_organization_users("northstar-live")

        self.assertIsNotNone(demo_user)
        self.assertEqual(demo_user.role, "owner")
        self.assertEqual(len(demo_team), 3)
        self.assertTrue(all(user.organization_id == "northstar-live" for user in demo_team))

    def test_account_store_creates_and_updates_team_member(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AccountStore(Path(temp_dir) / "users.json")
            member = store.create_user(
                name="Jordan Lee",
                email="jordan@example.com",
                password="welcome123",
                role="technician",
                organization_id="northstar-live",
                organization_name="Northstar Live",
            )
            updated = store.update_profile(
                member.id,
                name="Jordan L.",
                title="RF Technician",
                warehouse="North Warehouse",
            )

        self.assertEqual(updated.title, "RF Technician")
        self.assertEqual(updated.warehouse, "North Warehouse")

    def test_account_preferences_are_validated_and_persisted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "users.json"
            store = AccountStore(path, seed_defaults=True, allow_demo=True)
            updated = store.update_preferences(
                "admin",
                theme="dark",
                font_scale="largest",
                density="compact",
                show_progress=False,
            )
            reloaded = AccountStore(path, allow_demo=True).get("admin")

        self.assertEqual(updated.preferences["theme"], "dark")
        self.assertEqual(reloaded.preferences["font_scale"], "largest")
        self.assertFalse(reloaded.preferences["show_progress"])

    def test_integrations_are_scoped_to_the_organization(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "integrations.json"
            store = IntegrationStore(path)
            configured = store.configure(
                "northstar-live",
                "crm",
                {
                    "provider": "hubspot",
                    "endpoint": "https://api.hubapi.com",
                    "credential_env": "NORTHSTAR_CRM_TOKEN",
                },
            )

            northstar = IntegrationStore(path).get_all("northstar-live")
            salamandra = IntegrationStore(path).get_all("salamandra")

        self.assertEqual(configured["status"], "needs_credentials")
        self.assertEqual(northstar["crm"]["provider"], "hubspot")
        self.assertEqual(salamandra["crm"]["status"], "not_configured")

    def test_integration_credentials_cannot_probe_arbitrary_environment_variables(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = IntegrationStore(Path(temp_dir) / "integrations.json")
            configured = store.configure(
                "northstar-live",
                "crm",
                {
                    "provider": "generic",
                    "endpoint": "https://api.example.com",
                    "credential_env": "PATH",
                },
            )

        self.assertEqual(configured["credential_env"], "SALAMANDRA_CRM_API_KEY")

    def test_integration_rejects_local_or_non_https_crm_endpoints(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = IntegrationStore(Path(temp_dir) / "integrations.json")
            for endpoint in ("http://api.example.com", "https://127.0.0.1", "https://localhost"):
                with self.subTest(endpoint=endpoint):
                    with self.assertRaises(ValueError):
                        store.configure(
                            "northstar-live",
                            "crm",
                            {"provider": "generic", "endpoint": endpoint},
                        )

    def test_workspace_isolates_shared_inventory_by_organization(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = InventoryWorkspace(Path(temp_dir) / "inventories.json")
            workspace.add_item(
                ItemNode("PA Speaker", ItemType.PA),
                2,
                "shared",
                "admin",
                "salamandra",
            )
            workspace.add_item(
                ItemNode("PA Speaker", ItemType.PA),
                8,
                "shared",
                "demo-owner",
                "northstar-live",
            )

            salamandra = workspace.combined_inventory("admin", "salamandra")
            northstar = workspace.combined_inventory("demo-owner", "northstar-live")

        self.assertEqual(salamandra.get_item("pa speaker").count, 2)
        self.assertEqual(northstar.get_item("pa speaker").count, 8)

    def test_custom_item_class_persists_inside_organization(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "item-classes.json"
            catalog = ItemClassCatalog(path)
            catalog.upsert(
                ConfiguredItemClass(
                    id="wireless-intercom",
                    name="Wireless intercom beltpack",
                    family="communication",
                    capabilities=("comms.beltpack",),
                    dependency_rules=(
                        DependencyRule("power.battery", per_unit=2),
                    ),
                ),
                "northstar-live",
            )
            loaded = ItemClassCatalog(path)

        stored = loaded.get("wireless-intercom", "northstar-live")
        self.assertIsNotNone(stored)
        self.assertEqual(stored.capabilities, ("comms.beltpack",))
        self.assertIsNone(loaded.get("wireless-intercom", "salamandra"))

    def test_inventory_item_can_be_edited_without_losing_stock_counts(self):
        inventory = Inventory([ItemNode("Old PA", ItemType.PA, count=2)])

        updated = inventory.update_item(
            "old pa",
            ItemNode(
                "Warehouse PA A",
                ItemType.PA,
                class_id="main-pa",
                quality_score=82,
                weight_kg=21,
            ),
        )

        self.assertIsNone(inventory.get_item("old pa"))
        self.assertEqual(updated.id, "warehouse pa a")
        self.assertEqual(updated.count, 2)
        self.assertEqual(updated.class_id, "main-pa")


class TestEventOperations(unittest.TestCase):
    def test_event_record_prepares_checklists_conflicts_and_history(self):
        event = EventRecord(
            title="Show",
            description="Small show",
            start_date="2026-09-01",
            plan={
                "lines": [
                    {
                        "item_id": "pa speaker",
                        "amount": 2,
                        "missing": 1,
                        "conflict": True,
                    }
                ]
            },
        )

        event.prepare_operations("admin")

        self.assertEqual(len(event.checklist), 1)
        self.assertEqual(len(event.return_checklist), 1)
        self.assertEqual(len(event.conflicts), 1)
        self.assertEqual(event.history[-1]["action"], "prepared")

    def test_template_catalog_returns_event_templates_and_kits(self):
        catalog = TemplateCatalog()

        self.assertIsNotNone(catalog.get_template("conference-basic"))
        self.assertIsNotNone(catalog.get_kit("speech-kit"))

    def test_event_memory_isolates_reservations_by_organization(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            memory = EventMemory(Path(temp_dir) / "events.json")
            for organization_id in ("salamandra", "northstar-live"):
                memory.add(
                    EventRecord(
                        title=organization_id,
                        description="Conference",
                        start_date="2026-09-01",
                        organization_id=organization_id,
                        plan={
                            "lines": [
                                {"item_id": "pa speaker", "amount": 2, "missing": 0}
                            ]
                        },
                    )
                )

            northstar_reservations = memory.active_reservations(
                organization_id="northstar-live"
            )

        self.assertEqual(northstar_reservations, {"pa speaker": 2})


if __name__ == "__main__":
    unittest.main()
