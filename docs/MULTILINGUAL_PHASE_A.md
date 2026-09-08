# Phase A: Canonical Multilingual Data

Phase A is the completed foundation boundary. Import reconciliation UI (B),
event learning records (C), and Smart Planner V1 (D) are intentionally separate.
There is no trained model, translation API, vector database, cross-customer learning,
new historical recommendation algorithm, or automatic inventory merge in this phase.

## Data Contract

- `id`: existing stable inventory identity, retained for dependencies and ledgers.
  Existing identifier normalization is unchanged; there is no identity migration.
- `display_name`: original user-visible text, separate from identity. New clients
  send it explicitly; legacy clients can omit it and retain the existing ID label.
  Editing a name in the inventory form keeps the underlying ID unchanged.
- `canonical_type`: stable language-independent code, never a translated enum.
- `category_label`: original imported category label; no transliteration.
- Existing `type` is the compatibility family used by the accepted planner.

Built-in canonical codes include speaker, powered_speaker, monitor, microphone,
mixer, cable, lighting, power, vehicle, furniture, video, rigging, hospitality,
site, other, and the existing DI/backline/stand/case/accessory/decor/tool/display/
barrier families. Legacy `pa`, `transport`, and `catering` map to speaker, vehicle,
and hospitality. Custom categories receive server-generated `custom_<UUID>` codes.
Categories are not equipment capabilities: monitor/site/custom categories use the
conservative `other` compatibility family. Configure their item classes/capabilities
for allocation; a label alone does not invent equipment behavior.

Missing metadata defaults are computed on reads, not by rewriting existing holdings
or movement history. No stock ledger, event lifecycle, kit, session, or registration
architecture changes were made. New category metadata follows existing definition
edit restrictions and audit rules, including protection while stock is operational.

## UTF-8 Audit

Python source defaults to UTF-8. HTTP JSON bodies decode as UTF-8, and JSON responses
advertise UTF-8. JSON/log Unicode escapes are lossless serialization, not transliteration;
structured logs retain existing redaction and bounded context. PostgreSQL tests assert
UTF8 server encoding and round-trip Hebrew, Arabic, accented Latin, RTL/LTR and emoji.

CSV export uses UTF-8 BOM for Excel and retains formula-injection protection.
Import strips the BOM, preserves `display_name`/`category_label`, and resolves known
categories before the existing transactional reconciliation command. Export/re-import
keeps the explicit ID column. Browser uploads use a fatal UTF-8 decoder: invalid legacy
encodings produce an actionable error instead of silently replacing characters.

React renders text without HTML interpolation. Inventory names/notes, event briefs,
name/venue inputs, and CSV preview cells use content-aware direction where applicable.
The surrounding application remains LTR. Inventory search uses Unicode normalization
for matching and never changes stored display text. Full UI translation and arbitrary
language event understanding are not claimed; brief storage is multilingual, while
the existing parser's language coverage is unchanged.

## Catalog API

All routes require a signed-in active account and active organization membership.
The request context determines organization and actor; body organization IDs are not
used to select a workspace.

| Route | Permission | Behavior |
| --- | --- | --- |
| GET `/api/catalog` | inventory.read | Built-in codes and this workspace's terms |
| POST `/api/catalog/resolve` | inventory.read | Up to 100 labels, exact normalized resolution |
| POST `/api/catalog/categories` | catalog.manage | Create an immutable custom category |
| POST `/api/catalog/aliases` | catalog.manage | Confirm an alias to an existing canonical category |

`catalog.manage` belongs only to owners/admins. Writes require `label` and
`idempotency_key`, optionally `language`; alias writes also require `canonical_type`.
Canonical codes cannot be shadowed. Existing conflicting label mappings return 409;
remapping/deletion is not available yet. Exact retries return the original response;
changed payloads with the same key return 409. Category/alias, operation request and
audit rows commit together under the existing organization row lock.

Read resolution prefers canonical codes, then explicit workspace mappings, then
built-in multilingual aliases. Results identify `system` versus `workspace`, retain
the input label, and return `high` or `needs_review`, not a calibrated probability.
There are no automatic fuzzy matches. Unknown categories still need review; the
interactive reconciliation step is Phase B. Creating custom categories/aliases is
API-only until that maintenance surface is built. Local JSON mode supports built-in
resolution only; persistent workspace catalog writes require PostgreSQL.

## Schema and Performance

Alembic `0007_catalog_terms` adds one table for immutable category/alias terms,
with organization ownership, a unique normalized-label key, category/alias kind
constraint, and same-organization confirming-membership foreign key. Built-ins stay
versioned in source. Custom targets are validated inside the locked transaction;
there is no deletion path that can invalidate them.

At most 2,000 terms per organization are accepted. Each CSV request loads terms once,
builds a lookup index, and resolves categories in O(terms + rows), excluding text
length. There is no per-row database lookup for aliases. CSV remains bounded to
10,000 rows. Unicode matching uses NFKC, case folding, punctuation/space normalization;
it does not strip accents or merge similar model numbers.

Upgrade is additive and preserves existing staging inventory/events/history.
Downgrade removes the new catalog table and its mappings, so it is destructive for
Phase A catalog data; only perform downgrade testing on disposable databases.

```powershell
python -m alembic upgrade head
python -m alembic check
# Disposable test database only:
python -m alembic downgrade 0006_staging_usability
python -m alembic upgrade head
python -m alembic check
```

## Verification and Next Boundaries

New tests cover matching/display separation, multilingual aliases, Unicode CSV and
event round trips, UTF8 PostgreSQL encoding, org isolation, custom-code isolation,
lower-role rejection, conflicting retries and two-process alias creation. Existing
definition audit assertions additionally require the new canonical-type delta;
existing idempotency, stock/state, and Coffee-House assertions remain intact.
Browser QA includes mixed-language name creation/editing/search, stable identity,
RTL briefs, invalid-encoding rejection, and the prior responsive/keyboard checks.

Phase B: grouped category decisions, remembered mapping UI, cleanup view, duplicate
evidence, keep-separate decisions. Destructive merges remain deferred until their
history/dependency preservation can be proven.

Phase C: authoritative event features, proposal/correction/execution references,
optional feedback and explicit learning eligibility.

Phase D: bounded same-organization history retrieval, weighted structured similarity,
outcome-weighted requirements, explanations, cold-start rules and deterministic
fixtures. Phase A adds no historical planning behavior. The existing legacy history
suggestion path is unchanged; its eligibility and outcome treatment must be audited
and replaced or adapted during C/D before claiming Smart Planner V1 is complete.

## Changed Files

- `src/catalog_terms.py`, `src/postgres_catalog.py`: canonical matching and scoped catalog commands.
- `src/Item_node.py`, `src/database.py`, `src/postgres_runtime.py`, `src/security.py`,
  `src/server.py`: display metadata, storage, permission and HTTP/CSV integration.
- `migrations/versions/0007_catalog_terms.py`: additive catalog schema.
- `frontend/src/types.ts`, `frontend/src/pages/InventoryPage.tsx`,
  `frontend/src/pages/EventsPage.tsx`, `frontend/src/pages/SettingsPage.tsx`:
  display preservation, search, direction-aware fields and strict CSV decoding.
- `tests/test_catalog_terms.py`, `tests/test_postgres_api.py`,
  `tests/test_database_phase2.py`, `tests/e2e/operations.spec.ts`: regression coverage.
- `docs/MULTILINGUAL_PHASE_A.md`, `docs/STAGING_CHECKLIST.md`: phase boundary and QA record.
- `web/index.html`, `web/assets/app/*`: regenerated production frontend bundles.

Local verification: 166 Python tests, including 31 PostgreSQL tests and 13 race
tests, with zero skips/failures; 50 Playwright checks across 360/768/1280/1440px
and a 200%-equivalent viewport; TypeScript and Vite production build pass.
Migration upgrade/downgrade/re-upgrade and Alembic check pass on disposable PostgreSQL.
