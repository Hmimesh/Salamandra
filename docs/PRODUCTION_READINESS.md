# Salamandra Production-Readiness Review

> Historical snapshot from the original adversarial review. The remediated implementation and
> current verification status are documented in `SECURITY_PHASE1.md` and
> `POSTGRESQL_PHASE2.md`; findings below describe the pre-remediation repository.

Date: 2026-08-21

Scope: current uncommitted working tree on `main`

Review type: architecture and security review; no production architecture changes made

## Executive Summary

Salamandra is a capable local prototype, but it is not safe to expose as a multi-tenant production service. The domain model and planner are useful foundations. Tenant-filtered reads are present in several stores, passwords are not stored in plaintext, planner allocations use organization-scoped inventory, and the frontend uses same-origin requests. Those strengths do not compensate for production blockers at the API and persistence boundaries.

The highest-risk defects are a cross-organization event overwrite path, threaded mutation of shared in-memory state backed by non-atomic whole-file JSON writes, nearly absent server-side authorization, non-idempotent dispatch/return operations, and default accounts with known passwords created at startup. These can cause tenant isolation failures, unauthorized changes, double dispatch, incorrect returns, lost updates, or corrupted files.

The recommended direction is an incremental modular-monolith migration: keep the pure planning/domain logic, replace the ad hoc HTTP edge with typed request schemas and centralized authorization, and move authoritative state to PostgreSQL through SQLAlchemy and Alembic. Do not introduce microservices, Redis, or a queue in Phase 1.

## 1. Current Architecture As Observed

### Runtime topology

- `src/server.py` uses Python's `ThreadingHTTPServer` and `BaseHTTPRequestHandler` for API and static-file delivery.
- React 19 and TypeScript source lives in `frontend/`; Vite emits the production bundle into `web/`.
- The browser calls same-origin JSON endpoints through `frontend/src/lib/api.ts` and includes the session cookie.
- No Python web framework, ORM, migration tool, dependency manifest, CI workflow, container definition, or deployment configuration was found.

### State and persistence

- `AccountStore`, `InventoryWorkspace`, `EventMemory`, `ItemClassCatalog`, and `IntegrationStore` are process-global class attributes on `SalamandraServer`.
- Each store loads a JSON file into mutable dictionaries and rewrites the whole file on save.
- Accounts, inventories, events, custom item classes, and integrations are separate files under `docs/`.
- There are no transactions spanning stores, file locks, atomic temp-file replacement, schema versions, migrations, backups, or recovery checks.

### Authentication and authorization

- Sign-in verifies an email/password against `AccountStore` and stores a random token in an in-memory dictionary.
- The token is sent in an `HttpOnly; SameSite=Lax` cookie. It has no `Secure` attribute, expiry, rotation, device/session metadata, or persistent revocation record.
- Passwords use manually implemented PBKDF2-HMAC-SHA256 with 120,000 iterations.
- Five default/demo users with documented passwords are recreated when missing.
- Most POST endpoints require authentication, but role enforcement exists only for adding team members.

### Tenant boundaries

- Most normal reads and mutations derive `organization_id` from the authenticated `UserAccount`, which is the correct trust direction.
- Shared inventory, events, custom item classes, and integration settings are stored under organization keys.
- Personal inventory is keyed by user ID and is combined with the user's organization inventory for planning.
- Event lookup for checklist/status/return uses `event_from_body`, which checks event organization ownership.
- Event save is a separate path that deserializes an entire client event and can replace an existing record by ID without first authorizing that existing record.

### Operational workflows

- The planner parses descriptions, builds capability requirements, ranks real inventory, handles dependencies, substitutions, overlap priorities, and transport.
- Event plans, checklists, returns, conflicts, history, and calendar payloads are embedded JSON structures inside an event record.
- Inventory is an aggregate count per normalized item name. There is no durable reservation/allocation ledger linking quantities or serialized units to an event.
- Dispatch and return decrement/increment aggregate personal/shared inventory at request time.

### Test posture

- `tests/tests.py` contains 43 unit tests covering inventory primitives, planner regressions, the Coffee-House case, weighted overlap behavior, store-level organization filtering, preferences, integrations, and event checklists.
- No HTTP/API tests, negative authorization tests, cross-tenant attack tests, concurrency tests, migration tests, recovery tests, or browser security tests were found.
- The frontend has no automated component or end-to-end test suite.

## 2. Security Findings

Every finding below includes repository evidence, a realistic failure, the recommended correction, and the test needed to prove the correction.

### P0-01: Client-controlled event IDs allow cross-organization overwrite

**Evidence:** `src/server.py:357-367` constructs `EventRecord` from the request, overwrites its `organization_id` with the current user's organization, and calls `EventMemory.add`. `src/event_memory.py:166-168` assigns by `event.id` without checking the existing record. Unlike `event_from_body` at `src/server.py:571-577`, this path never authorizes the existing ID.

**Affected components:** event save API, `EventMemory`, tenant isolation, event history.

**Failure scenario:** a user in Organization A learns or guesses an Organization B event ID and sends it to `/api/events/save`. Organization B's record is replaced by an Organization A record, causing unauthorized modification and effective deletion.

**Recommended fix:** separate create and update commands. Generate IDs server-side on create. On update, load by `(organization_id, event_id)`, return `404` when not visible, authorize the requested action, accept an explicit update schema rather than a complete serialized record, and enforce same-tenant foreign keys in PostgreSQL.

**Tests required:** API tests that attempt to read, update, save, allocate, pack, dispatch, return, and export another organization's event by ID; assert `404` or `403` and byte-for-byte unchanged victim data.

### P0-02: Known default credentials are created on every account-store initialization

**Evidence:** `src/accounts.py:238-300` seeds admin, operator, owner, technician, and producer accounts with fixed salts and known passwords. `Readme.md` publishes several credentials. `src/server.py:38` and `src/server.py:990` initialize this store automatically.

**Affected components:** authentication, every protected endpoint, deployment configuration.

**Failure scenario:** a production deployment starts with the prototype data path. An attacker signs in with a documented account and receives owner/admin access.

**Recommended fix:** remove runtime account seeding from production startup. Put demo data behind an explicit development-only command and environment guard. Require first-owner provisioning through a one-time setup flow or deployment secret, with forced password change.

**Tests required:** production-config startup must contain zero seeded users; demo seeding must fail unless an explicit development mode is enabled; known prototype credentials must not authenticate in production mode.

### P0-03: Server-side authorization is largely absent

**Evidence:** after `require_user` at `src/server.py:102`, authenticated users may mutate shared inventory, custom classes, events, integration settings, and CSV data. The only role check is team invitation at `src/server.py:422-424`. `frontend/src/pages/TeamPage.tsx:10` hides invite controls, but other UI gating is not authoritative. `AccountStore.create_user` accepts any role string at `src/accounts.py:132-140`.

**Affected components:** all mutation endpoints, shared inventory, integrations, team administration, event state transitions.

**Failure scenario:** a read-only client or freelancer sends API requests directly to delete shared stock, change an integration endpoint, rewrite planning classes, dispatch an event, or import inventory despite the UI not showing those controls.

**Recommended fix:** define named permissions and enforce them centrally at every handler/service command. Validate assignable roles and prevent privilege escalation. Derive organization, actor, and effective permissions only from the authenticated server-side membership.

**Tests required:** a role-permission matrix with allow and deny cases for every endpoint; direct HTTP requests must prove UI state cannot bypass authorization; admins must not assign roles above their grant authority.

### P1-01: Session and login controls are prototype-only

**Evidence:** sessions are process-memory entries at `src/server.py:45` and `src/server.py:468-480`; cookies lack `Secure` and expiry; there is no idle/absolute timeout, rotation, global revocation, rate limit, recovery, MFA, or login audit. Sign-in performs an unbounded linear user scan at `src/accounts.py:88-96`.

**Affected components:** authentication, horizontal scaling, incident response.

**Failure scenario:** a stolen cookie remains valid until process restart; brute-force attempts are unlimited; multiple app processes do not share sessions; users cannot revoke other sessions after compromise.

**Recommended fix:** use an established authentication package or managed OIDC provider. For local credentials, use Argon2id through a maintained library, indexed email lookup, opaque database-backed sessions storing only a token hash, secure cookie settings, idle and absolute expiration, rotation on authentication/privilege change, rate limiting, password reset, and security-event logging.

**Tests required:** expiry, revocation, rotation, logout, concurrent sessions, brute-force throttling, generic failure responses, password rehash, cookie attributes, and recovery-token single-use tests.

### P1-02: CSRF and browser security policy are incomplete

**Evidence:** the session cookie is `SameSite=Lax`, but POST handlers do not validate CSRF tokens or `Origin`; no explicit CORS policy is defined; `send_json` and static responses set no CSP, HSTS, frame, sniffing, or referrer headers (`src/server.py:947-971`).

**Affected components:** all browser-authenticated mutations and static delivery.

**Failure scenario:** a same-site sibling application, future relaxed CORS configuration, or browser behavior change can submit authenticated mutations; clickjacking and content-sniffing defenses are absent.

**Recommended fix:** retain same-origin credentials, explicitly deny unapproved origins, validate `Origin` plus a CSRF token for unsafe cookie-authenticated methods, and apply a tested security-header policy at the reverse proxy/application edge. Set `Secure`, `HttpOnly`, and an appropriate SameSite policy in production.

**Tests required:** cross-origin preflight/read/write denial, missing/invalid CSRF rejection, valid same-origin mutation success, and automated header assertions over HTML/API responses.

### P1-03: The API trusts complete client-authored event structures

**Evidence:** `/api/events/save` accepts `owner_id`, `assigned_user_ids`, plan lines, conflicts, history, allocation updates, sync data, and calendar payload through `EventRecord.from_dict` (`src/event_memory.py:80-115`). The server only fills owner/assignees when empty and later persists the object.

**Affected components:** ownership, crew assignment, plan integrity, audit/history, integrations.

**Failure scenario:** a user fabricates plan lines, injects another user's ID, erases history, changes priority, or submits forged allocation updates. A later dispatch acts on this untrusted plan.

**Recommended fix:** use command-specific request schemas. Recompute plans, conflicts, history, actor, organization, and sync metadata server-side. Validate assigned users against current organization membership and the caller's permission.

**Tests required:** tampered fields must be ignored or rejected; non-member assignments must fail; history/actor/organization supplied by clients must never become authoritative.

### P1-04: Integration configuration exposes an environment-variable presence oracle

**Evidence:** callers may set arbitrary `credential_env` values at `src/integrations.py:73-89`; `get_all` reports whether that environment name exists. Any authenticated role may call `/api/integrations/configure` (`src/server.py:404-413`). CRM endpoints are accepted without host or scheme policy.

**Affected components:** integrations, process environment, future outbound HTTP.

**Failure scenario:** a low-privilege user probes names such as cloud credentials to discover deployment secrets' presence. A future sync implementation uses a hostile endpoint and creates SSRF or credential exfiltration.

**Recommended fix:** administrators choose from server-configured credential references, not environment names supplied by clients. Store OAuth tokens encrypted using a dedicated secret-management design. Validate providers and HTTPS destinations, block private/link-local networks for generic outbound integrations, and redact configuration in normal state responses.

**Tests required:** arbitrary environment names and private-network endpoints are rejected; secrets and token material never appear in API responses/logs; provider OAuth state and token rotation are tested.

### P1-05: Request and CSV boundaries are unbounded and unsafe for spreadsheet consumers

**Evidence:** `read_json_body` trusts `Content-Length` and reads it fully (`src/server.py:922-928`). CSV is transported as a JSON string, parsed completely, and has no row/field/file limits (`src/server.py:848-898`). Export writes operator-controlled values directly (`src/server.py:805-846`) without neutralizing cells beginning with `=`, `+`, `-`, `@`, tabs, or carriage returns.

**Affected components:** API availability, inventory integrity, CSV import/export.

**Failure scenario:** an oversized request exhausts memory or ties up a server thread; opening an exported CSV in Excel executes an injected formula; a malformed row mutates earlier in-memory rows before raising.

**Recommended fix:** enforce reverse-proxy and application request limits, stream multipart uploads into a staging table, validate all rows before committing, cap rows/columns/field lengths, and escape formula-leading exported cells according to the supported spreadsheet policy.

**Tests required:** oversized requests receive `413`; malformed imports make zero changes; formula payloads export as inert text; encoding, duplicate IDs, field limits, and large valid files are covered.

## 3. Data-Integrity And Concurrency Findings

### P0-04: Threaded requests mutate shared stores and rewrite files without synchronization

**Evidence:** `ThreadingHTTPServer` is used at `src/server.py:996`; stores are class-level mutable objects at `src/server.py:38-45`; saves use direct `open(..., "w")` and `json.dump` in accounts, inventory, events, classes, and integrations. No locks or transactions exist.

**Affected components:** all persistent data and every concurrent mutation.

**Failure scenario:** two operators dispatch or edit stock simultaneously. Both read the same quantity, interleave mutation/save, lose one update, truncate a JSON file, or persist an inconsistent combination of event and inventory state.

**Recommended fix:** move authoritative writes to PostgreSQL transactions. Until migration, do not expose the threaded JSON prototype as a shared service; a coarse process lock and atomic file replacement could reduce local-development corruption but is not the production solution.

**Tests required:** concurrent allocation/dispatch/return/update tests with barriers; assert no negative stock, no lost updates, valid persisted data, and invariant-preserving rollback on failure.

### P0-05: Dispatch and return are not idempotent or bound to durable allocations

**Evidence:** status changes at `src/server.py:301-338` can repeatedly execute stock movements. `/api/events/return` at `src/server.py:270-285` has no status guard. `InventoryWorkspace.return_to_available_scopes` searches aggregate in-use quantities rather than quantities owned by the event (`src/inventory_workspace.py:138-155`). Status transitions are not constrained to a legal sequence.

**Affected components:** allocation, packing, dispatch, returns, stock accuracy.

**Failure scenario:** a retry or double-click dispatches twice. A repeated return can consume in-use quantity belonging to another event with the same item ID. A user moves an event from `out` back to `planning` without returning stock.

**Recommended fix:** persist event allocations with explicit states and source inventory/location. Implement a server-side state machine and idempotent transition command in one transaction. Lock relevant allocation/stock rows, verify current version/state, record a movement ledger, and make repeated requests return the existing result.

**Tests required:** simultaneous double-dispatch, double-return, retry-after-timeout, illegal transitions, partial-line failure rollback, and two events sharing the same SKU. All must preserve per-event ownership and total-stock invariants.

### P1-06: Multi-store operations can partially mutate before failure

**Evidence:** dispatch/use loops mutate one plan line at a time and save afterward (`src/server.py:255-267`, `src/server.py:311-321`). A later insufficient line raises after earlier lines changed in memory. Event saving and allocation updates write `EventMemory` repeatedly, independently from inventory.

**Affected components:** dispatch, return, overlap reallocation, persistence recovery.

**Failure scenario:** line one is deducted, line two fails, the API returns an error, and a later unrelated save persists the partial deduction. Reallocation writes some affected events before another operation fails.

**Recommended fix:** place validation and all related writes in one database transaction. Lock all rows in deterministic order, validate the complete command, then write stock movements, allocation state, event version, and audit event atomically.

**Tests required:** fault injection at each operation step must leave all tables unchanged; deadlock-retry behavior and deterministic lock order must be verified.

### P1-07: Aggregate item names cannot guarantee physical-unit ownership

**Evidence:** `Inventory` stores `dict[item_id, ItemNode]` and merges counts by normalized item name (`src/Inventory.py:15-50`). Combined inventory merges personal/shared quantities with the same ID (`src/inventory_workspace.py:238-279`). Plans retain only item ID and amount, not source scope/location/unit.

**Affected components:** high-value serialized equipment, personal inventory, warehouse locations, dispatch/return accountability.

**Failure scenario:** two identical speakers owned by different people/locations are combined. Packing selects one physical unit, dispatch deducts another scope, and return credits whatever aggregate in-use bucket is found first.

**Recommended fix:** distinguish item definitions/SKUs from stock holdings and optional serialized assets. Allocation rows must identify source holding/location and, when applicable, physical unit IDs. Preserve personal ownership explicitly.

**Tests required:** same SKU in multiple scopes/locations, serialized unit conflict, personal-owner access, partial allocations, transfers, and exact-unit returns.

### P1-08: Operational history is not a security audit trail

**Evidence:** event history is embedded, client-replaceable event JSON with action/actor/note/timestamp only (`src/event_memory.py:130-138`). Inventory, account, integration, export, and permission changes have no immutable audit record.

**Affected components:** accountability, incident response, compliance, customer support.

**Failure scenario:** an event overwrite erases history; unauthorized stock edits cannot be attributed; there is no durable record of before/after values or request identity.

**Recommended fix:** create an append-only audit-event table written in the same transaction as sensitive actions. Record actor membership, organization, action, resource type/ID, timestamp, request/correlation ID, result, and redacted before/after or structured change data. Keep user-facing operational notes separate.

**Tests required:** every protected command emits one audit event on success, failed attempts can be security-logged without state mutation, tenant-scoped audit reads are enforced, and audit rows cannot be edited through normal APIs.

## 4. Scalability Findings

### P2-01: Full-state reads and responses grow with the entire organization

**Evidence:** `/api/state` and most mutations call `state_payload`, which serializes all visible inventory, events, users, classes, templates, and integrations (`src/server.py:585-632`). `EventMemory.to_dict` sorts and serializes all events and recalculates reservations.

**Failure scenario:** normal checklist clicks become slow as event history grows; every mutation sends megabytes and blocks one thread on JSON encoding.

**Recommended fix:** expose paginated resource endpoints and return only the changed resource/version from commands. Keep a small bootstrap endpoint for user/organization/navigation metadata. Add indexed filters for active date windows, status, warehouse, and search.

**Tests required:** query-count and response-size budgets over realistic large fixtures; pagination stability and tenant filtering; no full event scan for a single checklist mutation.

### P2-02: Planning and history searches repeatedly scan and sort whole collections

**Evidence:** each capability requirement scans all inventory and sorts candidates (`src/event_planner.py:448-478`); alternatives may copy the full candidate list into each line. Overlap and reservation methods scan every event (`src/event_memory.py:224-272`), and historical suggestions tokenize every event (`src/event_memory.py:186-210`).

**Failure scenario:** large inventories, dependency-expanded requirements, and years of events produce high CPU, oversized plan payloads, and latency spikes.

**Recommended fix:** query only available organization inventory matching indexed capability/class/location conditions; query overlapping active events by indexed time bounds/status; cap returned alternatives; precompute searchable normalized terms only when measurements justify it. Keep planning synchronous initially with a latency budget; move unusually heavy imports/exports or re-plans to jobs only after evidence.

**Tests required:** benchmark fixtures at target sizes, planner result-equivalence tests, capped payload assertions, and query-plan/index checks.

### P2-03: Whole-file persistence is O(total data) per mutation and single-process only

**Evidence:** every `save` rewrites the complete store. Sessions and stores live in one process. Multiple processes would diverge immediately.

**Failure scenario:** adding one checklist tick rewrites all events; large files increase lock time and corruption exposure; a second application instance has stale data and unrelated sessions.

**Recommended fix:** PostgreSQL row-level writes and shared database-backed sessions. Scale the stateless application process horizontally only after database invariants and observability exist.

**Tests required:** persistence latency at representative sizes, multi-process API tests against one database, and rolling-restart session behavior.

## 5. Production Risks

| Risk | Severity | Likelihood now | Impact |
| --- | --- | --- | --- |
| Cross-tenant event replacement | P0 | High once IDs leak through logs, links, or support | Tenant data loss and isolation breach |
| Concurrent JSON mutation/write | P0 | High with multiple operators | Lost updates, corrupted files, wrong stock |
| Unauthorized role actions | P0 | Certain for any authenticated low-privilege user who calls the API | Inventory/integration/event compromise |
| Non-idempotent dispatch/return | P0 | High under retries and real operator usage | Double movements and event stock mix-ups |
| Default known accounts | P0 | Certain if deployed unchanged | Full workspace compromise |
| Weak request/integration boundaries | P1 | Medium | DoS, CSV injection, future SSRF/secret exposure |
| Missing operational controls | P1 | High during incidents | Slow detection and unreliable recovery |
| Full-state/scanning architecture | P2 | Increasing with adoption | Latency and memory pressure |

## 6. Proposed Target Architecture

Use a modular monolith with one deployable Python API and one PostgreSQL database.

### Application boundaries

1. **API edge:** FastAPI with Pydantic command/response schemas, explicit request limits, standardized errors, security middleware, and generated OpenAPI. This replaces only `BaseHTTPRequestHandler`; it does not rewrite planner logic.
2. **Application services:** commands such as `CreateEvent`, `UpdateEvent`, `PlanEvent`, `PackEvent`, `DispatchEvent`, `ReturnEvent`, and `ImportInventory`. Each command receives an authenticated actor context and owns its authorization/transaction boundary.
3. **Domain:** preserve and tighten planner, capability matching, dependency, transport, and event-state rules as framework-independent Python.
4. **Persistence adapters:** SQLAlchemy 2 repositories and a unit-of-work over PostgreSQL; Alembic owns migrations. Repository methods require organization context and never accept client-derived tenant identity.
5. **Frontend:** keep React/TypeScript, consume narrow typed endpoints, treat all frontend role checks as presentation only, and handle optimistic concurrency conflicts explicitly.

### Core data model

- `organizations(id, name, created_at)`
- `users(id, email, password_hash/auth_subject, status, created_at)`
- `memberships(id, organization_id, user_id, status, version)` with unique `(organization_id, user_id)`
- `roles`, `permissions`, `role_permissions`, and `membership_roles`
- `warehouses(id, organization_id, name)` and optional `stock_locations`
- `item_classes(id, organization_id nullable, ...)`; null organization means immutable system class
- `items(id, organization_id, class_id, name, metadata, version)`
- `stock_holdings(id, organization_id, item_id, warehouse/location, owner_user_id nullable, quantity, version)`
- `inventory_units(id, organization_id, item_id, serial/tag, state, location, version)` for serialized assets
- `events(id, organization_id, owner_membership_id, status, starts_at, ends_at, priority, version, ...)`
- `event_requirements(id, organization_id, event_id, capability/item, quantity, level, source)`
- `allocations(id, organization_id, event_id, status, version, idempotency_key)`
- `allocation_lines(id, organization_id, allocation_id, holding_id/unit_id, quantity, state)`
- `stock_movements(id, organization_id, holding/unit, event_id, action, quantity, actor, created_at, idempotency_key)`
- `checklist_items(id, organization_id, event_id, allocation_line_id, phase, done, version)`
- `integration_connections(id, organization_id, provider, encrypted_credential_reference, configuration, status)`
- `audit_events(id, organization_id, actor_membership_id, action, resource_type, resource_id, request_id, changes, created_at)`

Every tenant-owned relation should carry `organization_id`. Use composite same-tenant foreign keys or equivalent database constraints, checks for non-negative quantities, unique active allocation constraints for serialized units, and indexes beginning with `organization_id` for tenant queries. PostgreSQL row-level security may be added as defense in depth after application scoping is proven; it must not replace application authorization.

### Transaction and concurrency model

- Plan previews are read-only and may use a consistent transaction snapshot.
- Approval/reservation locks relevant stock holdings in deterministic ID order, recalculates availability, and writes allocations atomically.
- Packing uses allocation-line versions; it cannot change a dispatched/returned allocation.
- Dispatch and return are legal state-machine transitions with unique idempotency keys and movement-ledger entries.
- Quantity edits use optimistic versions for ordinary forms and row locks when competing with allocation/movement.
- External calls never run inside stock transactions. Use an outbox table later if reliable asynchronous synchronization becomes necessary; do not add a queue in Phase 1.

## 7. Database Migration Strategy

1. Freeze JSON schema changes and add explicit schema/version metadata to export tooling.
2. Introduce PostgreSQL, SQLAlchemy models, Alembic, and repositories behind existing domain interfaces.
3. Build an idempotent JSON-to-database importer that maps organizations/users first, then classes/items/holdings, events/requirements/plans, and finally history/integrations.
4. Validate counts, missing references, duplicate normalized IDs, cross-organization assignments, malformed dates, and illegal event states into a migration report. Do not silently coerce uncertain records.
5. Run dual-read comparison in development: load JSON and database fixtures, then compare organization totals, planner inputs, event counts, reservations, and Coffee-House output.
6. Perform a rehearsal migration from a copy, record duration and checksums/counts, then restore-test the database.
7. For cutover, stop writes, back up JSON, run importer, execute invariant queries, run smoke/regression tests, and enable the database-backed service. Keep JSON read-only for rollback; never dual-write indefinitely.

Required migration constraints and indexes include non-negative quantities, valid status enums/checks, unique normalized item identity within its intended scope, unique membership, unique idempotency keys per organization/action, indexed event time windows/status, indexed capability/class/warehouse inventory lookups, and same-tenant foreign keys.

## 8. Authentication And Authorization Strategy

### Authentication

- Prefer a maintained identity provider when B2B requirements include MFA, SSO, or SAML. For initial local credentials, use maintained password/session libraries rather than custom cryptography.
- Store normalized email with a unique index. Hash passwords with Argon2id and support transparent rehash.
- Store only a hash of opaque session tokens in PostgreSQL with user, issued time, idle/absolute expiry, revocation time, and last-used metadata.
- Use HTTPS-only secure cookies, CSRF protection for unsafe methods, login throttling, recovery token single use, generic authentication errors, and audit events.
- Remove all automatic production seeding. Keep explicit demo fixtures separate from migrations.

### Authorization

Use capabilities rather than route-local role strings. Initial permission groups:

- `organization.manage`, `members.read`, `members.invite`, `members.roles.manage`
- `inventory.read`, `inventory.shared.write`, `inventory.personal.write`, `inventory.export`, `inventory.import`
- `events.read`, `events.create`, `events.update`, `events.assign`, `events.plan`
- `operations.pack`, `operations.dispatch`, `operations.return`, `operations.override`
- `integrations.read`, `integrations.manage`, `audit.read`

Suggested roles map to these permissions: owner, administrator, producer/event manager, warehouse/operator, technician/freelancer, and read-only/client. Application services authorize both permission and resource relationship. Owners are not trusted because the frontend says they are owners; effective membership is loaded server-side for each request.

## 9. Test Strategy

### Unit tests

- Preserve all planner and Coffee-House regressions.
- Add pure state-machine, permission-policy, CSV neutralization, validation, and allocation-invariant tests.

### API/integration tests

- Run the real ASGI application against an isolated PostgreSQL database.
- Cover authentication lifecycle, command validation, transaction rollback, and narrow response contracts.
- Generate a permission matrix for every endpoint and role.

### Tenant-isolation tests

- Create Organization A and B fixtures with colliding human-readable names.
- Attempt every list/get/create/update/delete/allocate/export/pack/dispatch/return action using victim IDs in URLs, query strings, and JSON fields.
- Include event overwrite, assigned users, item classes, personal inventory, integrations, audit log, exports, and search/autocomplete.

### Concurrency tests

- Use separate database connections and barriers to race two allocations, dispatches, returns, quantity edits, and checklist updates.
- Assert stock conservation: `on_hand = available + reserved/packed/dispatched` according to the chosen model, never negative, and each serialized unit has at most one active allocation.
- Test idempotency and transaction retries.

### Migration/reliability tests

- Import representative and malformed JSON snapshots; compare totals and planner results.
- Test forward migration on a production-like copy and restore from backup.
- Fault-inject integration timeouts and database exceptions; assert rollback and useful error IDs.

### Frontend/end-to-end tests

- Test sign-in/out, role-sensitive presentation, planning, packing, dispatch, return, conflict handling, and stale-version recovery.
- Remember that UI tests demonstrate workflow quality, not server authorization.

## 10. Deployment And Operations Requirements

- Build immutable application artifacts; pin Python and Node dependencies; generate an SBOM.
- Run behind HTTPS with explicit proxy trust, request/time limits, security headers, and restricted allowed hosts/origins.
- Add `/health/live` for process liveness and `/health/ready` for database/migration readiness without leaking internals.
- Emit structured JSON logs with timestamp, level, request ID, actor/membership ID when known, organization ID, route/command, latency, status, and redacted error context.
- Add error tracking and alerts for authentication spikes, authorization failures, transaction conflicts, migration failures, and integration errors.
- Keep secrets in a managed secret store or deployment secret mechanism; never in repository JSON, browser state, logs, or arbitrary user-selected environment names.
- Use encrypted PostgreSQL backups, point-in-time recovery where available, documented retention, and scheduled restore tests.
- Run dependency vulnerability, secret, static analysis, typecheck, unit, integration, tenant-isolation, and migration checks in CI.
- Make migrations backward-compatible for rolling deployment where practical; block startup when required migrations are missing; back up before destructive migrations.
- Use timeouts/retries with bounded exponential backoff for external integrations. Record failures and permit safe retry without repeating local side effects.

## 11. Prioritized Implementation Roadmap

### Phase 1: Secure transactional core

1. Add the FastAPI application edge, typed schemas, authenticated actor context, and centralized permission checks for inventory/event operations.
2. Introduce PostgreSQL, SQLAlchemy, and Alembic for organizations, users/memberships, items/holdings, events, allocations, movement ledger, sessions, and audit events.
3. Split event create/update, generate IDs server-side, and close the cross-tenant overwrite path.
4. Implement transactional, versioned, idempotent reserve/pack/dispatch/return commands.
5. Remove production demo seeding and add negative tenant/RBAC/concurrency tests while preserving the Coffee-House regression.

### Phase 2: Complete migration and hardening

- Migrate item classes, checklists, integrations, preferences, and historical data.
- Add CSRF/security headers, rate limiting, recovery, session management, request/upload limits, safe CSV handling, and audit viewers.
- Add structured logging, health/readiness, error tracking, backups, restore tests, and CI security checks.

### Phase 3: Measured scale improvements

- Replace full-state responses with paginated resources and narrow command responses.
- Add tenant-first indexes and scoped candidate/overlap queries.
- Benchmark planning and cap alternatives/payloads.
- Introduce background execution only for measured long-running imports, exports, or integrations, using a database outbox before considering extra infrastructure.

## Unverified Areas

- No production hosting, reverse-proxy, TLS, identity-provider, database, backup, monitoring, or secret-management configuration exists in the repository, so their safety is unverified.
- There is no Python dependency lock/manifest, so transitive vulnerability exposure and reproducibility are unverified.
- Google Calendar, CRM, and Google Sheets do not perform live OAuth/API synchronization in the inspected code; token handling and external failure behavior are therefore unverified rather than secure.
- The target number of organizations, users, inventory lines, events, warehouses, and peak concurrent operators is not documented. Performance thresholds must be agreed before optimizations are accepted.
- Legal/compliance requirements, data residency, retention/deletion policy, and whether customer personal data will be stored are not defined.

## Production Verdict

**NOT PRODUCTION READY**

## Top Five Risks

1. Cross-organization event overwrite through client-controlled event IDs.
2. Concurrent shared-memory mutation and non-atomic JSON persistence.
3. Missing server-side RBAC across almost every mutation endpoint.
4. Non-idempotent dispatch/return without durable event-owned allocations.
5. Automatically seeded accounts with public, known passwords.

## Recommended Phase 1

Build one narrow vertical slice for secure inventory movement: authenticated actor and permission middleware, PostgreSQL/Alembic persistence for organizations/memberships/events/stock/allocations, server-generated event IDs, and transactional idempotent reserve-pack-dispatch-return commands. Prove it with cross-tenant negative tests, permission-matrix tests, concurrency races, rollback tests, and the existing Coffee-House regression. Keep the current planner as a pure domain service and do not add new product features.
