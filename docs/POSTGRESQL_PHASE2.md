# PostgreSQL Transaction Migration Plan

Date: 2026-08-21

## Objective

Replace all authoritative JSON/global-memory state with the PostgreSQL transactional core without
changing user-facing product behavior or rewriting the planner.

The schema, initial Alembic migration, SQLAlchemy models, transaction service, JSON importer, and
portable constraint/idempotency tests now exist. The remaining work is repository/API cutover and
real PostgreSQL concurrency verification.

## Implemented Foundation

- Pinned SQLAlchemy, Alembic, psycopg, and Argon2 dependencies in `requirements.txt`.
- Alembic environment and `0001_transactional_core` migration.
- Tenant-owned organizations, users, memberships, inventory holdings, events, item classes,
  integrations, allocations, allocation lines, stock movements, audit events, and sessions.
- Inventory buckets for available, reserved, packed, and dispatched quantities.
- Non-negative quantity/version checks and legal status checks.
- Same-organization composite foreign keys for event owners, allocation events/holdings, movement
  actors, and audit actors.
- Unique idempotency keys per organization and unique event allocation ownership.
- `SELECT ... FOR UPDATE` transaction service for reservation, packing, dispatch, and return.
- Idempotent duplicate dispatch/return handling.
- JSON importer with dry-run reporting, organization reconstruction, exact quantity migration,
  secret-field exclusion, and explicit warnings for unattributed in-use stock.
- Imported legacy event plans are marked unverified and cannot reserve inventory until replanned by
  the server.

## Cutover Sequence

### 1. Database-backed identity and request context

- Implement repositories for user, membership, role, session, and organization reads.
- Hash opaque session tokens in the database and load the current membership for every request.
- Put `{user_id, membership_id, organization_id, permissions}` in an immutable request context.
- Remove `UserAccount.organization_id` as the source of authority after membership cutover.

### 2. Database-backed inventory reads and writes

- Map holdings to the existing domain `Inventory` only for read-only planner snapshots.
- Replace add/edit/remove/import commands with row-level application services.
- Use optimistic `version` checks for forms and `FOR UPDATE` when quantities compete with allocation.
- Keep planner/domain objects detached from SQLAlchemy models.

### 3. Database-backed event creation and planning

- Persist server-generated events and normalized requirements in one transaction.
- Store the generated plan with `plan_verified=true`, planner version, and inventory snapshot/version
  metadata.
- On confirm, lock the event and relevant holdings in deterministic ID order, recalculate availability,
  and create allocation lines atomically.
- A conflict rolls back the complete allocation and returns HTTP 409 with no partial reservation.

### 4. Transactional state transitions

- Route confirm/pack/dispatch/return through `TransactionalEventOperations`.
- Confirm: available to reserved and create exact tenant-owned allocation lines.
- Pack: reserved to packed after checklist/version validation.
- Dispatch: packed to dispatched, write movement/audit rows, and consume one idempotency key.
- Return: dispatched to available using the same allocation lines, write movement/audit rows, and
  consume one idempotency key.
- Never make external integration calls inside these transactions.

### 5. Remaining stores

- Move custom item classes, integrations, preferences, checklists, event history, and activity feeds
  to PostgreSQL repositories.
- Keep operational history user-facing; write security audit records separately and append-only.
- Store only encrypted OAuth/token references. Never migrate raw secret fields.

### 6. Migration rehearsal

1. Back up all JSON files and record checksums.
2. Apply Alembic to a clean isolated PostgreSQL database.
3. Run `migrate_json_to_postgres.py --dry-run` and resolve every ownership/time/status warning.
4. Run the real import once, then rerun it to prove idempotency.
5. Compare organization/user/event/item counts and per-holding quantity totals.
6. Replan every active imported event; do not reserve an unverified legacy plan.
7. Reconcile unattributed `in_use_count` quantities to exact events/holdings manually.
8. Run Coffee-House, tenant isolation, RBAC, migration, and PostgreSQL concurrency suites.

### 7. Production cutover

- Enter a maintenance window and stop JSON writes.
- Take final JSON and PostgreSQL backups.
- Run final import and invariant queries.
- Start the database-backed API with JSON writes disabled.
- Smoke-test authentication, inventory, plan, confirm, pack, dispatch, return, export, and audit.
- Keep JSON read-only for a time-bounded rollback window; do not dual-write.

## Transaction Boundaries

- **Create event:** event, requirements, verified plan metadata, history, audit.
- **Confirm/reserve:** event lock, holding locks, allocation, lines, quantity buckets, movement, audit.
- **Pack:** event/allocation/line/holding locks, checklist versions, quantity buckets, movement, audit.
- **Dispatch/return:** event/allocation/line/holding locks, idempotency key, quantities, movement, audit.
- **Inventory edit/import:** staged validation, holding locks/versions, rows, audit; all or nothing.
- **Membership/role change:** membership lock/version, permission validation, session revocation, audit.

All holding locks must be acquired in deterministic holding-ID order. Transaction retries must be
bounded and must reuse the same client request/idempotency key.

## Required PostgreSQL Verification

- Run `test_postgres_concurrent_reservation` with `SALAMANDRA_TEST_POSTGRES_URL` against an isolated
  database; exactly one of two events competing for one unit must reserve it.
- Race duplicate dispatch and return through separate connections; assert one movement per action.
- Race quantity edit against reservation; assert either a clean version conflict or a valid serialized
  result, never negative stock.
- Verify same-tenant composite foreign keys and unique idempotency constraints directly in PostgreSQL.
- Run `EXPLAIN ANALYZE` for organization inventory lookup and overlap-window queries at agreed scale.

## Completion Gate

Phase 2 is complete only when the running API no longer instantiates `AccountStore`,
`InventoryWorkspace`, `EventMemory`, `ItemClassCatalog`, or `IntegrationStore` as authoritative
production stores; every mutation is transaction-backed; PostgreSQL race tests pass; backup restore
is rehearsed; and JSON is import/export compatibility data only.
