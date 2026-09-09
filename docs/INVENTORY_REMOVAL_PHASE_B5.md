# Phase B.5: Inventory Removal

## Lifecycle Policy

- Item quantity reduction continues through `/api/inventory/remove` and the existing
  transactional adjustment ledger. Only available stock can be removed. Removing
  the last unit archives the definition if its dependency references allow it.
- `/api/inventory/archive` archives an already-empty active definition. It requires
  the existing shared/personal inventory write permission and an idempotency key.
  Reserved, packed, or dispatched quantities are not empty stock.
- Neither command hard-deletes a holding or historical references. An empty archive
  creates definition/batch audit records but no fictitious quantity movement.
- The combined inventory view shows the actual source quantity in the removal
  dialog. Shared stock is selected first; use My inventory for personal stock.

## Owner-Only Workspace Clear

`GET /api/inventory/clear.preview` returns active shared definition and available
unit counts, a version-sensitive preview token, and any blocking dependency or
operational-stock explanation. `POST /api/inventory/clear` requires that token,
an idempotency key, and the exact confirmation `CLEAR INVENTORY`.

The centralized `inventory.clear` permission belongs only to owners. Admins,
operators, producers, and technicians cannot invoke either clear endpoint.
Request organization and actor come from authentication, not submitted fields.
The service independently rechecks active membership, active user, and permission.

Clear affects **shared inventory only**. Personal stock is preserved. Users,
memberships, events, kits, templates, catalog mappings, historical movements and
audits are not deleted or rewritten. Existing planning documents may reference
archived definitions; future allocation must still resolve real active stock.

## Dependencies and Transaction Boundary

One database transaction contains authorization, operation receipt lookup, current
preview validation, dependency validation, every stock adjustment, archival,
definition/batch audits, and the completed receipt. It acquires the organization
row with `FOR NO KEY UPDATE`, then locks affected holdings in deterministic ID order.
This remains compatible with event movement foreign-key locking.

The complete shared dependency graph may archive together. A remaining active
personal definition referencing a shared target blocks the entire clear. Any
reserved, packed, or dispatched quantity also blocks it. Stale previews require a
fresh review. Validation failures and exceptions roll back all writes, including
ledger entries and receipts. There is no partial-success mode.

Receipts follow the existing organization/operation/key namespace. Identical
retries return the original batch result, even after archival; changed payloads
conflict. Personal archive fingerprints include the actual inventory owner.

The new commands require the PostgreSQL runtime and never use JSON persistence.
No schema migration, background job, external dependency, or Phase C feature was
introduced.

## Verification

- `tests/test_inventory_removal.py`: owner enforcement, preserved personal/foreign
  stock, shared graph archival, personal dependency rejection, all operational
  buckets, stale/incorrect confirmation, rollback after ledger insertion,
  zero-stock archival, and personal receipt ownership.
- `tests/test_postgres_api.py`: real two-process duplicate-clear contention,
  reservation-versus-clear row-lock contention, reserved/packed/dispatched
  rejection, HTTP role forgery and tenant boundaries, dependency handling,
  malformed identity, empty archival, audit/ledger/receipt counts, and replay.
  Each new HTTP test uses the existing full database fixture cleanup assertion.
- `tests/e2e/operations.spec.ts`: quantity reduction and last-unit archival,
  empty-definition archival, source-specific quantity limits, typed confirmation,
  focus trapping/restoration, Escape, lost-response retry, personal preservation,
  and admin visibility at all five responsive projects.

Canonical local commands:

```powershell
$env:SALAMANDRA_TEST_POSTGRES_URL = '<disposable PostgreSQL URL>'
$env:SALAMANDRA_DATABASE_URL = $env:SALAMANDRA_TEST_POSTGRES_URL
$env:SALAMANDRA_REQUIRE_POSTGRES_TESTS = '1'
.\.venv\Scripts\python.exe tests/run_production_core_acceptance.py
.\.venv\Scripts\python.exe -m alembic check
pnpm run check
pnpm run qa:browser
```

`qa:browser` includes TypeScript compilation, Vite production build and the full
Playwright suite. Existing CI runs the same acceptance checks on disposable
PostgreSQL and the browser suite on Linux and Windows. Required PostgreSQL/race
tests cannot silently skip in canonical acceptance.

Local verification on 2026-09-09: 196 Python tests passed, including all 39
PostgreSQL-required and 17 race-required tests; zero skips and failures. All 60
Playwright checks passed at 360, 768, 1280, 1440 and the 200% viewport, including
light/dark appearance and large text. TypeScript, Vite build and Alembic check
passed. Browser fixtures exited cleanly. The Windows CI browser step allows ten
minutes for the expanded suite (local browser duration: 7.7 minutes), with no
retries or relaxed assertions.
