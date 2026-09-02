# Security And Data Integrity Phase 1

Date: 2026-08-21

## Review Method

The Senior findings were treated as hypotheses. The server was exercised through real HTTP
requests using two organizations and multiple roles before fixes were applied. The baseline
security suite produced eight failures and one request-thread error across eleven tests.

The JSON concurrency finding was reproduced independently with two stale
`InventoryWorkspace` instances writing the same file. Writer A saved `writer-a`, writer B then
saved `writer-b`, and a reload contained only `writer-b`. This proves a lost-update path even
without requiring a partially written JSON file.

## Findings Reproduced

| Finding | Baseline evidence | Result |
| --- | --- | --- |
| Anonymous tenant event disclosure | `GET /api/state` returned the Organization B event and its ID in sync state | Reproduced |
| Cross-tenant event overwrite | Organization A saved Organization B's event ID and replaced its title/organization | Reproduced |
| Missing server RBAC | A technician created shared inventory directly with HTTP 200 | Reproduced |
| Forged operational plan | `/api/events/save` persisted a client-supplied nonexistent item and quantity | Reproduced |
| Invalid state transition | A planning event moved directly to `out` | Reproduced |
| Duplicate dispatch | Two identical `out` requests consumed inventory twice | Reproduced |
| Duplicate return | First return succeeded and the retry failed after attempting the movement again | Reproduced |
| Malformed identifier handling | A dictionary event ID raised `TypeError` and closed the request connection | Reproduced |
| Known demo credentials | Fixed credentials were seeded in `AccountStore` and documented | Reproduced from code and tests |
| Stale static credential exposure | An obsolete generated frontend bundle still contained the former demo password | Reproduced by static asset scan |
| JSON lost updates | Two stale store writers caused writer A's item to disappear | Reproduced |

## Findings Not Reproduced As Stated

- Anonymous POST mutation was already rejected with HTTP 401.
- Using a nonexistent inventory ID was already rejected with HTTP 404 and did not change stock.
- The existing `event_from_body` path already blocked cross-organization checklist/status/return
  lookup. It returned HTTP 401, which incorrectly implied failed authentication; it now returns a
  non-disclosing HTTP 404. The separate event-save path was vulnerable to cross-tenant overwrite.
- Authenticated `/api/state` reads were already filtered to the signed-in organization. The
  anonymous `None` filter was the disclosure path.

## Fixes Implemented

### Authentication and anonymous access

- Signed-out state now contains empty tenant resources and no filesystem path.
- Presets, templates, exports, and sync status require authentication and permission.
- Sessions have a 12-hour expiry; production cookies can be marked `Secure`.
- Cross-origin unsafe requests are rejected and security headers are applied.
- Tenant JSON and CSV responses use `Cache-Control: no-store` so signed-in workspace data is not
  retained as a reusable browser cache response.
- Five failed sign-ins in five minutes trigger HTTP 429 for that IP/email pair.
- New passwords use Argon2id. Legacy PBKDF2 hashes are upgraded after successful authentication.
- Demo accounts are no longer password-authenticatable. Demo seeding/sign-in requires the explicit
  `SALAMANDRA_ENABLE_DEMO=1` local switch, and generated passwords are not documented.
- Sign-in and team-member forms ship with empty password fields. Obsolete generated bundles are
  removed after the verified frontend build.

### Tenant isolation and authorization

- `src/security.py` defines one permission vocabulary and role-policy map.
- Every API endpoint was audited. Shared inventory, imports/exports, item classes, planning,
  event creation, operational transitions, integrations, team management, and sync enforce named
  permissions server-side.
- Personal inventory writes always use the authenticated user's ID.
- Tenant-owned event lookup uses both event ID and authenticated organization ID.
- Event create rejects an existing foreign ID without revealing whether the foreign record exists.
- Role assignment is validated and only owners may grant owner/admin access.

### Authoritative event operations

- Event save is create-only. The server generates the event ID, organization, owner, assignees,
  status, plan, conflicts, history, and movement state from a description and allowed overrides.
- Client event plans, organization IDs, statuses, history, and movement fields are ignored.
- Legal transitions are `planning -> confirmed -> packed -> out -> returned`.
- Checklist phase and event state are validated before edits.
- Dispatch records exact personal/shared source allocations in the event movement record.
- Return replays that dispatch record; aggregate in-use stock from another event cannot be used.
- Retrying `out` or `returned` returns success without another inventory movement.
- Kit checkout accepts only a server-owned kit ID and idempotency key. It creates an event,
  advances it through the legal state machine, and persists the exact dispatch movement. The
  former client-plan `/api/events/use` mutation is removed.

### Adjacent input/integration fixes

- JSON requests are limited to 1 MB and must contain an object.
- Identifiers and positive quantities are validated before store access.
- CSV imports stage and validate all rows before changing inventory, with row/field limits and
  duplicate-ID rejection.
- CSV exports neutralize spreadsheet formula prefixes.
- Integration credential references are server-selected; clients cannot probe arbitrary environment
  names. CRM endpoints require public HTTPS destinations and cannot contain URL credentials.

## Tests Proving The Fixes

`tests/test_security_phase1.py` proves:

- `test_unsigned_state_does_not_expose_tenant_events`
- `test_unsigned_mutation_is_rejected`
- `test_org_a_cannot_read_org_b_event`
- `test_org_a_cannot_overwrite_org_b_event`
- `test_technician_is_denied_every_administrative_or_shared_write_route`
- `test_technician_can_write_only_their_personal_inventory`
- `test_forged_allocation_plan_is_not_persisted`
- `test_invalid_status_transition_is_rejected`
- `test_duplicate_dispatch_moves_inventory_once`
- `test_duplicate_return_restores_inventory_once`
- `test_duplicate_kit_checkout_creates_one_event_and_movement`
- `test_legacy_client_plan_checkout_is_removed`
- `test_malformed_event_id_returns_validation_error`
- `test_nonexistent_inventory_id_does_not_change_stock`
- `test_csv_import_is_atomic_when_a_later_row_is_invalid`
- `test_csv_export_neutralizes_spreadsheet_formulas`
- `test_demo_sign_in_is_disabled_without_explicit_demo_mode`
- `test_frontend_does_not_ship_known_demo_credentials`
- `test_repeated_failed_sign_in_is_rate_limited`
- `test_cross_origin_mutation_is_rejected`
- `test_security_headers_are_sent`

`tests/tests.py` additionally proves Argon2 hashing, explicit demo seeding, organization-scoped
inventory/classes/integrations, fixed credential references, local-endpoint rejection, and all
existing planner/operations regressions including Coffee-House.

`tests/test_database_phase2.py` proves schema constraints, same-tenant ownership, rollback,
idempotent transition movements, JSON migration behavior, Alembic upgrade/downgrade, personal
inventory ownership, and real PostgreSQL row-lock behavior when
`SALAMANDRA_TEST_POSTGRES_URL` points to the disposable test database.

`tests/test_postgres_api.py` starts two independent API processes against one isolated PostgreSQL
schema. It proves durable database sessions, database-backed inventory and events, transactional
confirmation/dispatch/return, one movement under a simultaneous duplicate dispatch, restart
durability, and the absence of JSON persistence writes.

## Remaining Risk

Production startup now requires `SALAMANDRA_DATABASE_URL` and uses PostgreSQL repositories for
identity, sessions, inventory, events, allocations, movements, item classes, integrations, and
preferences. JSON compatibility storage is available only with the explicit
`SALAMANDRA_ALLOW_JSON_DEV=1` or demo switch; its event transition is protected by one process lock
but remains unsuitable for multi-process production use.

Deployment still requires a backup/import rehearsal for the real warehouse dataset and operational
monitoring of the production database. Those deployment tasks do not leave an active application
path writing authoritative production mutations to JSON.
