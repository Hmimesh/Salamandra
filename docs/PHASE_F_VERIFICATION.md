# Phase F Implementation and Verification

## Review Remediation

Remediation base: `674f5e8eb706846e386b24dfea9753b20509457d` on
`staging-deployment`. Changes are limited to export permissions, shortened external
access windows, original-intake proposal binding, Events return inspection and
in-memory external credentials. See `OPERATIONS_REALITY_PHASE_F.md` for the exact
binding, revocation and refresh rules. No migrations or services were added.

The five focused backend reproductions failed before the corrections on both
SQLite and PostgreSQL. Their corrected suites passed 43 tests. Added HTTP tests
cover CSV roles/non-disclosure/formula-safe Unicode, proposal rollback/corrections/
replay/actor and tenant scope, and two-process issuance versus shortening with
stale-version rejection. All newly inserted HTTP fixture state is torn down by
the existing database-isolation decorator.

Canonical verification used PostgreSQL 16.15 on loopback port 55433, disposable
schemas and actual production API processes. `tests/run_production_core_acceptance.py`
passed 340/340 tests: 110/110 PostgreSQL-required and 31/31 race-required, zero
skips/failures. The composed field-release/supplemental-dispatch/inspected-return
HTTP lifecycle passed. Independent Alembic upgrade/check/downgrade-to-base/upgrade/
check passed through unchanged head `0017_event_crew` with no pending operations.

TypeScript and Vite passed before browser verification; assets were held fixed
during each browser run. A focused browser reproduction exposed same-tab fragment
reopening after reload; handling hash changes now captures and removes the fragment
before fetching. The corrected focused matrix passed 10/10 checks across all five
viewports. Browser SQLite fixtures are UI evidence only, not PostgreSQL evidence.
The final full Playwright matrix passed 135/135 in 15.4 minutes with zero failures
or skips: 360px, 768px, 1280px, 1440px and 640x450 CSS pixels at doubled DPR
(200%-equivalent). Existing lifecycle, learning, theme, multilingual, keyboard and
inventory regressions remained enabled. The fixture shut down cleanly on port
4174; previews on port 8000 were not changed.

Generated bundle replacements are content-hash changes from the two page updates
and extraction of the shared ReturnInspection chunk, not deletion of product
functionality. UX-audit screenshots and ignored local test/debug artifacts are
excluded from the remediation commit.

## Frozen Implementation Baseline

Branch: `staging-deployment`. Integrated debugging is complete; this document
records the verified implementation being frozen for the authorized commit/push.
No manual deployment, merge, Railway/DNS change or Senior review is included.

## Delivered

- Field requests are separate from physical fulfillment. Packed releases and
  supplemental packing/dispatch use authoritative, atomic stock movements.
  Dispatched reductions wait for inspected return. Original dispatch is immutable.
- Logistics milestones and reservation windows are independent of show duration.
  Standby retains packed stock. Schedule changes are versioned, idempotent and
  audited. Operational CSV includes equipment, crew call/release and changes.
- Crew profiles support internal/external personnel, skills and complexity.
  Event roles specify headcount and skills. Assignments use call/release windows,
  scoped notes and explicit equipment visibility. Overlaps/skill mismatches require
  a privileged, audited override. Headcount cannot be exceeded by an override.
- External links are read-only, hashed, revocable and expire at issuance's release
  time plus 24 hours. Regeneration revokes previous credentials. Tokens are not
  stored in receipts or audit records and never grant workspace authentication.
- Inventory shows separate ready/reserved/packed/standby/out/condition quantities.
  Standby is subtracted from displayed packed counts to avoid double counting.

## Integration and Boundaries

The existing inspected-return command was preserved. Allocation reads exclude
released lines and include supplemental quantities. Return closes pending field
requests inside the existing transaction. Condition incidents and original
movements remain intact. Tests reconcile damage after crew/logistics changes and
after supplemental dispatch without returning damaged equipment to ready stock.

Every mutation checks centralized permissions, organization ownership and current
membership. Retry receipts, row updates and audit entries commit together.
Version conflicts return 409. Reused keys with different payloads conflict.
Assignment writes lock the event and crew profile before overlap/headcount checks.
External and operational-summary reads use a committed PostgreSQL snapshot without
write locks. No new database/cache/service was introduced.

New APIs: `/api/events/adjustments` (request/list, preview, fulfill, cancel),
`/api/events/logistics` (read/edit, stage, export), `/api/crew`,
`/api/events/crew` (read, role, assign, access), `/api/external/assignment`,
and `/api/inventory/operations`.

New permissions: `events.adjust`, `logistics.edit`, `crew.read`, `crew.manage`,
`crew.assign`, `crew.override`, `crew.access`. Existing pack/dispatch/return and
inventory permissions continue to govern authoritative physical operations.

Remaining-slice migrations: 0015 field adjustments/released lines, 0016 logistics
windows, 0017 normalized crew and credential tables. Prior uncommitted Phase F
migrations 0012-0014 remain included and unchanged in purpose.

## Verification

Final Python acceptance: **327 passed, 102 PostgreSQL-required, 30 race-marked,
0 skipped, 0 failures**. These are test-method counts; several race-marked methods
exercise multiple races. PostgreSQL 16.15 ran locally on disposable test schemas.
The HTTP fixture applies real Alembic migrations before starting API processes.
The integrated scenario exercises packed release, supplemental dispatch, crew
overrides, logistics, external expiry/revocation, tenant isolation and inspected
return. Twelve units reconcile to nine ready, two needing repair and one missing;
original movements and audit payloads remain unchanged. Retry and stale-version
checks preserve stock and condition incidents.

Commands:

```powershell
$env:SALAMANDRA_TEST_POSTGRES_URL = '<disposable PostgreSQL URL>'
$env:SALAMANDRA_DATABASE_URL = $env:SALAMANDRA_TEST_POSTGRES_URL
$env:SALAMANDRA_REQUIRE_POSTGRES_TESTS = '1'
.\.venv\Scripts\python.exe tests/run_production_core_acceptance.py
.\node_modules\.bin\tsc.cmd -p frontend/tsconfig.json --noEmit
.\node_modules\.bin\vite.cmd build --config frontend/vite.config.ts
$env:SALAMANDRA_E2E_PORT = '4174'
.\node_modules\.bin\playwright.cmd test
```

TypeScript and Vite passed. Alembic upgrade/check/downgrade-to-base/upgrade/check
passed through 0017 in an isolated PostgreSQL schema, with no pending migration.

Full Playwright matrix: **130/130 passed, 0 skipped, 0 failures** (15.0 minutes).
The targeted crew flow also passed 5/5. Projects are 360px, 768px, 1280px, 1440px,
and 640x450 CSS pixels at DPR 2 (200%-equivalent layout, not browser chrome zoom).
Browser fixtures use disposable SQLite with the SQL runtime; separate PostgreSQL
HTTP/two-process suites prove database locking and transaction behavior.

New browser flows cover crew profile/skill entry, role creation, assignment,
Unicode notes, link issuance, a separate external browser session and revocation;
logistics editing/export; field adjustment review/supplemental dispatch; and
regression coverage of inspected returns. Existing planner, inventory, import,
kit, dependency, keyboard, theme and text-size checks remain enabled.

Issues fixed during verification: missing new tables in the migration expectation;
the wrapped select test locator now uses its accessible combobox role; external
header image constrained after the 360px overflow check failed. A transient Windows
HTTP connection abort was reproduced as a failed run, then the staging HTTP suite
and complete canonical suite passed without weakening assertions or adding skips.
During integrated debugging, one Windows socket abort did not recur in twenty
targeted repetitions or the final complete gate. An initial browser run hit a
login 404 while generated assets were being rebuilt; a complete rerun against the
unchanged build passed. Build and browser verification must run sequentially.

## Phase F File Scope

Modified source/tests:

```text
frontend/src/App.tsx
frontend/src/components/AppShell.tsx
frontend/src/components/InventoryDependencyPicker.tsx
frontend/src/pages/EventsPage.tsx
frontend/src/pages/InventoryPage.tsx
frontend/src/pages/ReturnsPage.tsx
frontend/src/pages/TeamPage.tsx
frontend/src/styles.css
frontend/src/types.ts
src/database.py
src/event_learning.py
src/event_memory.py
src/postgres_removal.py
src/postgres_runtime.py
src/security.py
src/server.py
tests/e2e/operations.spec.ts
tests/test_database_phase2.py
tests/test_postgres_api.py
```

New source/tests/documentation:

```text
docs/OPERATIONS_REALITY_PHASE_F.md
docs/PHASE_F_VERIFICATION.md
frontend/src/components/CrewProfiles.tsx
frontend/src/components/EventCrew.tsx
frontend/src/components/EventLogistics.tsx
frontend/src/components/EventRequirementsEditor.tsx
frontend/src/components/FieldAdjustments.tsx
frontend/src/components/InventoryOperationsSummary.tsx
frontend/src/components/ReturnInspection.tsx
frontend/src/pages/ExternalAssignmentPage.tsx
frontend/src/pages/MaintenancePage.tsx
migrations/versions/0012_event_proposals.py
migrations/versions/0013_equipment_conditions.py
migrations/versions/0014_condition_event_origin.py
migrations/versions/0015_field_adjustments.py
migrations/versions/0016_logistics_windows.py
migrations/versions/0017_event_crew.py
src/equipment_conditions.py
src/event_crew.py
src/event_logistics.py
src/event_proposals.py
src/event_returns.py
src/field_adjustments.py
tests/test_equipment_conditions.py
tests/test_event_crew.py
tests/test_event_logistics.py
tests/test_event_proposals.py
tests/test_field_adjustments.py
```

Vite also regenerates `web/index.html` and hashed `web/assets/app/` bundles.
The existing `docs/ux-audit-2026-09-03-new-ui/` files were not changed.

## Freeze Scope

Include the source, tests, migrations and documentation listed above, plus the
tracked frontend build. Vite replaces 33 obsolete hashed bundles with the current
bundle graph. Exclude the pre-existing UX-audit directory and all local databases,
test traces/screenshots, logs and generated debug reports. No source, test or
migration changes were needed during freeze inspection; documentation was updated
to the final integrated-debug evidence and local preview credentials removed.

## Scope Limits

No known required remaining-slice implementation gap is identified; all requested
local verification gates passed. Optional assigned
repair personnel, partial incident-batch resolution, pagination, notifications,
finance, procurement and early partial returns are not added. Crew profiles do
not create login accounts. A future deployment must apply migrations explicitly;
this verification did not migrate private staging or production data.
