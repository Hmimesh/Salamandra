# Phase B: Inventory Reconciliation and Import Mapping

This phase adds reviewed, additive shared-inventory imports and administrator
catalog maintenance. It does not add event learning, Smart Planner, trained ML,
translation services, destructive definition merging, or cross-tenant learning.

## Operator Flow

Settings > Import to shared inventory opens the file chooser, then:

1. Map columns. Original CSV cells remain visible. Unmapped columns are ignored.
2. Review categories, grouped by source value with row counts. Known exact aliases
   have suggestions; unknown values need a category, a new custom category, or skip.
3. Review duplicates and malformed rows. Choose add quantity, keep separate/create,
   or skip. Ambiguous duplicate candidates never silently select a target.
4. Confirm totals and the final row preview. Unresolved rows disable Import.

Back remains available before commit. Every modification invalidates the previous
request key; an unchanged failed request retains its key for an identical retry.
Back/forward navigation alone does not replace that key.
Cancel before committing writes nothing, including draft category/alias choices.
The server reconstructs the plan on commit; the client cannot declare trusted totals.

CSV input is strict UTF-8, limited to 2 MB, 10,000 nonblank rows, 50 unique named
columns and 4,096 characters per cell. Quantities are positive whole numbers up to
1,000,000. Invalid quantities/row widths can be skipped, or the user can return to
mapping/upload. Structural quoting errors reject the file before any mutation.
Condition values are ready/service/retired; unrecognized values require correcting
the file or ignoring that optional column. Encoding conversion is not implemented.
The existing HTTP API additionally caps the entire JSON request at 1,000,000 bytes,
including escaped CSV and review decisions. This can require smaller batches before
the parser's 2 MB file limit is reached; Phase B does not raise that production limit.

Header suggestions use normalized built-in English, Hebrew and Arabic vocabulary,
with selected Spanish quantity/name labels. All mappings can be overridden. Header
vocabulary is not learned globally or persisted per workspace in this version.
No internal ID is required: new definitions receive server-generated item IDs.
An exported canonical_type column is preferred over the legacy type adapter.

## Categories and Product Evidence

Phase A remains authoritative for canonical types, matching normalization and
workspace aliases. Owner/admin choices can create custom categories and remember
aliases at final commit. Other inventory operators can use existing mappings and
choose built-in categories but cannot persist organization-wide catalog changes.
Unknown categories are never guessed using an external translation or fuzzy model.

Duplicate evidence comes from indexed normalized names/model identifiers:

- Exact: same Unicode-normalized display name.
- Likely: equivalent punctuation/spacing or known Electro-Voice/EV formatting;
  matching model plus matching manufacturer also provides likely evidence.
- Possible: matching model with incomplete/different manufacturer evidence.

Numeric model differences are hard negative evidence. ZLX-12P is not collapsed into
ZLX-15P. Evidence labels describe rules, not calibrated probabilities. Category
differences prevent suggestions. Nothing automatically merges stock.

Within-file targets must be earlier rows explicitly kept as new entries. Add-to-
existing targets are same-organization active shared holdings. Review tokens bind
the target identity and definition, not its changing stock count. Renamed, edited,
archived, missing or foreign targets require a new review or fail non-disclosingly.
Concurrent legitimate stock additions are therefore preserved instead of overwritten.

## Cleanup

Inventory > Inventory cleanup is owner/admin-only and reports actual current shared
inventory: candidate duplicates, unknown categories, and optional missing manufacturer,
model and location. No fictitious unresolved-alias count is displayed. Missing product
details link directly to the existing inventory editor. Generic equipment need not
have a manufacturer or model. Search and user content are Unicode/RTL aware.

Keep separate and same intended product decisions are workspace-scoped. Each pair is
bound to definition fingerprints; changed definitions become reviewable again. Same
product decisions add evidence for future matching, never permission to merge. Stock
and historical identities are untouched. Category aliases can also be added here.
There is no destructive merge, quantity transfer between existing definitions, or
global product alias vocabulary. Imported keep-separate choices create distinct IDs;
only cleanup pair decisions are remembered persistently in this version.

## Transaction Boundary

POST /api/inventory/import.review is read-only. POST /api/inventory/import.commit:

1. Begins one database transaction and locks the organization row FOR NO KEY UPDATE.
   This serializes imports while allowing event movements' foreign-key key-share locks.
2. Rechecks active identity/membership and inventory import/shared-write permissions.
3. Checks the organization + operation + idempotency key and payload fingerprint.
4. Locks current shared holdings in ID order, then rebuilds the CSV plan against
   bounded, current scoped inventory/catalog data. This prevents stale quantity reads
   while event reservation/return transactions own a holding lock.
5. Creates authorized category/alias records inside this same transaction.
6. Validates targets, creates new identities, and sums additions per holding.
7. Adds quantities through the accepted inventory adjustment ledger/audit helpers.
8. Saves the completed operation receipt and batch audit, then commits once.

Any failure rolls back the entire batch, including catalog records and their nested
operation receipts. No separately committed service calls are chained. Exact retries
return the completed result without another adjustment; changed payloads return 409.
Every actual quantity increase has a ledger record tied to actor, holding, reason,
request key and import batch. Existing definition metadata/history is never replaced
by an add-quantity decision. JSON writes are not used by the new production paths.

The older /api/inventory/import.csv replacement/reconciliation command remains a
compatibility API with its existing regression coverage. The new wizard exclusively
uses the additive reviewed-import command. Personal inventory import is not offered
by this wizard; its target scope is explicitly shared.

## Schema and Bounds

Alembic 0008_catalog_decisions adds a single table with an organization foreign key,
unique organization/pair fingerprint, same/separate action constraint, and bounded
evidence data. Decision changes are audited through the existing audit table. There
are at most 2,000 remembered pairs per organization. No existing inventory/event data
is rewritten. Downgrade drops these new decisions only, so test it on disposable DBs.

Inventory/category definitions are batch-loaded, not queried once per CSV row. Review
supports at most 20,000 active shared definitions and 2,000 catalog terms. Indexed
name/model lookups inspect at most 40 candidates and return at most five per row.
Candidate records are flat, preventing recursively nested duplicate chains.
Expected review complexity is O(inventory + CSV rows + remembered decisions), with
bounded text lengths/candidate work. DB writes remain proportional to changed holdings
and newly confirmed catalog groups. Cleanup displays 200 candidate pairs per pass;
row detail lists and import rows/categories render in pages of 25.

## Verification

- Unit/transaction tests: multilingual grouping, distinct model numbers, malformed/
  oversized CSV, flat duplicate chains, custom/alias rollback, shared add/retry,
  conflicting payloads, within-file additions, cleanup permissions/scoping/staleness.
- PostgreSQL HTTP tests: foreign/custom/stale/archived targets, forged scopes and
  malformed identifiers, permission changes between preview and commit, no partial
  catalog writes, replay and conflicting replay.
- Deterministic two-process race: the test holds the organization row until BOTH
  API commands are waiting on the real PostgreSQL lock. Duplicate import moves stock
  once. A second import racing a normal stock addition preserves both deltas and the
  ledger sum.
- Event reservation/import race: an uncommitted real reservation owns the holding
  lock. The HTTP import must wait on that holding, then preserve reserved stock and
  add only its requested quantity after reservation commits.
- Browser scenario: Hebrew headers/categories, Arabic category/name, accents, custom
  category, duplicate formatting, different model number, ignored column, invalid
  quantity, formula-like cell, back navigation, import, export and cleanup edit link.
- Browser fixtures use the transactional runtime on disposable SQLite for portable
  UI coverage; PostgreSQL concurrency is separately verified against real PostgreSQL.
  Fixture preference resets await each request to avoid overlapping setup mutations.
  Each multilingual import viewport has its own workspace to prevent cross-test
  duplicate candidates. Wizard step focus is synchronous with the layout update,
  so a delayed animation callback cannot steal focus from the next control.

The canonical CI jobs remain Linux/PostgreSQL and Windows browser QA. Playwright retries
are disabled locally and in CI, so a flaky pass cannot hide an initial failure.

Local verification on Windows, 2026-09-09:

- Complete canonical acceptance: 183/183 tests passed, 35/35 PostgreSQL-required,
  15/15 race-required, zero skips and failures, against real PostgreSQL 16.15.
- TypeScript check and Vite production build passed.
- Complete browser suite: 55/55 passed in 7.1 minutes, zero retries. Five additional
  focused import/cleanup checks with explicit console assertions passed in 34.1 seconds.
- Viewports: 360, 768, 1280 and 1440 pixels; 640x450 CSS viewport at device scale 2
  represents the 200% effective viewport. Existing light/dark, largest text, keyboard,
  focus trap/restoration, Escape and viewport-overflow assertions remain enabled.
- Migration: upgrade head, check, downgrade to 0007, re-upgrade to 0008 and check passed.
- Synthetic 10,000-row/model-duplicate review: 0.884 seconds locally; serialized review
  was 12.4 MB. This is an observed local measurement, not an SLA. Large review responses
  remain a limitation; server-side paging is deferred. Only 25 rows render at once.
- ERR_NO_BUFFER_SPACE did not recur locally. Fixture child exit was zero and port 4173 closed.

Initial independent CI at 6b0e1a5 passed all PostgreSQL tests; one Linux job passed
55/55 browser tests. Another Linux job exposed delayed dialog focus. Two Windows
jobs each passed 54/55: one exposed overlapping preference writes in the test itself,
the other a Winsock ERR_NO_BUFFER_SPACE failure on a lazy page-module request.
These failed runs are retained, not converted into passes by retry configuration.

Follow-up browser corrections:

- Dialog initial focus now runs during layout, before newly shown controls can be
  focused by the operator. Wizard heading focus runs only on step changes, preserving
  the external launch control for focus restoration. The import test asserts this.
- An unreadable successful import response does not close the wizard or claim a
  confirmed import. The operator can retry the identical key; a browser fault-injection
  test commits the first command, corrupts its response, retries and checks stock once.
- Every tested preference change awaits its HTTP response, even if the requested
  value already matches the visible DOM. No production preference behavior changed.
- The test-only listener backlog is 64 instead of Python 3.12's default five, to
  accommodate browser asset bursts. This is capacity hardening, not proof of the
  original Windows failure's cause. Production listener configuration is unchanged.
- Failed browser tests retain request counts/peak concurrency and failed resource
  paths. A Windows buffer-space failure additionally captures socket-state counts,
  dynamic TCP range and browser/Python handle/memory metrics. No tokens are captured.
- Retries remain zero. Windows resource exhaustion remains an environment risk until
  subsequent zero-retry runs and diagnostics establish reproducibility.

Follow-up local verification: 183 Python tests passed again in 83.943 seconds,
35 PostgreSQL-required and 15 race-required with zero skips. Full Windows browser QA
passed 55/55 in 6.7 minutes. After the receipt-validation correction, all five
multilingual lost-response/retry scenarios passed in 34.5 seconds. Three repeated
tablet sign-out/page-module probes passed in 23.5 seconds without ERR_NO_BUFFER_SPACE.
TypeScript and Vite passed again. No browser retry setting was enabled.

Chromium maps Windows WSAENOBUFS to ERR_NO_BUFFER_SPACE; that code alone does not
prove port exhaustion. See [Chromium's error mapping](https://chromium.googlesource.com/chromium/src/net/+/master/base/net_errors_win.cc)
and [Microsoft's diagnostic guidance](https://learn.microsoft.com/en-us/troubleshoot/windows-client/networking/tcp-ip-port-exhaustion-troubleshooting).

The Phase B PostgreSQL HTTP regressions are:

- test_reviewed_import_two_process_replay_and_stock_addition_races
- test_reviewed_import_waits_for_event_holding_lock
- test_reviewed_import_http_security_and_atomic_rollback
- test_reviewed_import_rechecks_permissions_after_preview

Linux Playwright and the independent CI reruns require the authorized branch push;
their final run URL and outcome belong in the completion report, not a local-only
production-acceptance claim.

## Files

New: src/inventory_reconciliation.py, src/postgres_reconciliation.py,
frontend/src/components/InventoryImportWizard.tsx,
frontend/src/pages/InventoryCleanupPage.tsx, frontend/src/lib/catalog.ts,
frontend/src/reconciliation.css, migrations/versions/0008_catalog_decisions.py,
tests/test_inventory_reconciliation.py, this report.

Updated: src/database.py, src/postgres_catalog.py, src/postgres_runtime.py,
src/server.py, frontend/src/App.tsx, frontend/src/main.tsx,
frontend/src/pages/InventoryPage.tsx, frontend/src/pages/SettingsPage.tsx,
frontend/src/components/ui.tsx,
tests/test_database_phase2.py, tests/test_postgres_api.py, tests/e2e_server.py,
tests/e2e/operations.spec.ts, playwright.config.ts, docs/STAGING_CHECKLIST.md, generated web/ bundles.
