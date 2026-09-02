# Salamandra Web Deployment Readiness Review

Review date: 2026-08-30

Reviewer posture: independent acceptance review. This document is not an implementation plan and does not approve deployment by itself.

## Hardening Implementation Update

The Web Staging Hardening phase has addressed the code-level HIGH findings from
this review. Production-like startup now validates a canonical HTTPS origin, exact
allowed origins, trusted hosts, secure cookies, PostgreSQL, runtime mode, and
explicit trusted-proxy addresses. It rejects demo and JSON compatibility modes.

`GET /health` is public liveness only. `GET /ready` checks database connectivity
and the exact repository Alembic head without exposing internals. Staging and
production startup use the same schema gate before serving. CI now supplies a real
PostgreSQL service, runs the complete Python suite and migration rehearsal, and
uses `pnpm install --frozen-lockfile` for TypeScript and production builds.

Structured logs cover startup/shutdown, authentication, session invalidation,
authorization denial, readiness/schema failures, event conflicts, inventory
conflicts, and unexpected errors. Sensitive field names are dropped and request
bodies, session tokens, cookies, passwords, and database URLs are not logged.

This update does not approve deployment. External staging evidence is still
required for TLS/ingress, access restriction, logs/alerts, backup restore, browser
smoke tests, and rollback rehearsal. Follow `docs/STAGING_RUNBOOK.md` and
`docs/STAGING_CHECKLIST.md`.

Inventory-definition edits are now transactional and audited. Protected identity,
class, capability, substitution, condition, and handling fields cannot change
while stock is reserved, packed, or dispatched. The field policy and separate
quantity/ownership commands are documented in
`docs/INVENTORY_DEFINITION_INTEGRITY.md`.

## 1. Scope And Verdicts

This review covers the current working tree for the web/API deployment path, PostgreSQL cutover, authentication/session model, tenant isolation, inventory/event transactional core, and the operational controls needed before exposing Salamandra on a real domain.

WEB STAGING VERDICT: CODE-READY FOR RESTRICTED STAGING; ENVIRONMENT GATES REMAIN

Private staging can proceed only after the staging blockers in Section 15 are satisfied. This means a controlled, non-production data environment with named testers, HTTPS, PostgreSQL, no demo mode, restricted access, and explicit rollback. It does not mean public signup, broad internet exposure, or production warehouse reliance.

PRODUCTION PILOT VERDICT: READY WITH BLOCKERS

The reviewed application-security and data-integrity core is materially stronger than the JSON/runtime baseline, but a controlled production pilot still needs operational work: backup/restore rehearsal, deployment hardening, migration/reconciliation proof for real warehouse data, monitoring, and release/rollback controls.

Code gates completed for private staging:

- Central fail-closed deployment configuration and secure cookies/origin/Host validation.
- Public liveness, database/schema readiness, and startup schema revision gate.
- PostgreSQL-backed multi-process tests required by CI.
- pnpm lockfile installation, TypeScript check, build, and Alembic rehearsal in CI.
- Provider-neutral staging runbook and safe environment-variable template.

Environment evidence still required before named testers are admitted:

- Real staging TLS/ingress and private access restriction.
- Disposable PostgreSQL test execution and migration output for the release candidate.
- Central log collection/alerts plus backup, restore, browser, and rollback rehearsals.

What prevents `READY FOR CONTROLLED PILOT`:

- No completed backup/restore rehearsal for the target PostgreSQL environment.
- No real warehouse JSON-to-PostgreSQL reconciliation signoff.
- No structured security/operational logging and alerting baseline.
- No documented deployment rollback and migration rollback procedure tested against staging.
- No production access/account provisioning procedure beyond owner/admin-created accounts with temporary passwords.

## 2. Current Architecture Observed

Observed code path:

```text
Browser -> Salamandra Python HTTP server -> PostgreSQL
        -> same process serves built React assets from web/
```

The server is a `BaseHTTPRequestHandler` application using `ThreadingHTTPServer` in `src/server.py`. It serves the production frontend from `web/` and handles `/api/*` in the same process. In production mode, `src/server.py:1461-1499` selects `PostgresRuntime` when `SALAMANDRA_DATABASE_URL` is present. If the database URL is missing, startup fails unless `SALAMANDRA_ALLOW_JSON_DEV=1` or `SALAMANDRA_ENABLE_DEMO=1` is explicitly set.

This is acceptable for private staging if it runs behind a real HTTPS reverse proxy/process supervisor. It is not acceptable to expose the Python server directly to the internet as the public edge.

Intended staging architecture:

```text
Internet
  -> DNS for salamandra-staging.hmimesh.com
  -> HTTPS reverse proxy or managed ingress
  -> one Salamandra app process initially
  -> PostgreSQL database with migrations applied
  -> central logs, backups, and restricted admin access
```

Do not add microservices, Redis, queues, or Kubernetes solely to make this deployable. A single modular service plus PostgreSQL is the right scale for a controlled pilot.

## 3. Production-Capable, Staging-Only, And Dev-Only Surfaces

Production-capable code paths:

- PostgreSQL selected by `SALAMANDRA_DATABASE_URL` and rejected if the URL is not PostgreSQL in `src/database.py:472-475`.
- Durable hashed sessions in `src/postgres_runtime.py:726-759`.
- Organization-scoped users, memberships, inventory holdings, events, allocations, stock movements, item classes, integrations, profiles, and preferences through `PostgresRuntime`.
- Transactional event transitions in `src/database.py:660-767`.
- Atomic kit checkout through `TransactionalKitOperations.checkout` in `src/database.py:1452-1600`.
- PostgreSQL inventory add/remove/import ledger through `TransactionalInventoryOperations` in `src/database.py:942-1440`.
- Direct inventory checkout/return disabled under PostgreSQL in `src/server.py:239-264` and `src/postgres_runtime.py:350-358`.
- Security headers and origin checks in `src/server.py:1423-1455`.

Accept only for private staging:

- `ThreadingHTTPServer` direct app server.
- In-memory login throttling in `src/server.py:546-568`.
- Admin-created accounts with temporary passwords through `/api/team/invite`.
- Same-process static asset serving.
- Manual migration and startup sequencing.

Dev/demo only:

- JSON compatibility storage behind `SALAMANDRA_ALLOW_JSON_DEV=1`.
- Demo sign-in behind `SALAMANDRA_ENABLE_DEMO=1`.
- Direct HTTP on `127.0.0.1`.
- Local JSON fixture data under `docs/*.json`.

## 4. Domain And DNS Readiness

Use a staging subdomain first, for example `salamandra-staging.hmimesh.com`. Use a separate production host later, for example `salamandra.hmimesh.com`.

Required DNS posture:

- Staging DNS points only to the chosen staging ingress or reverse proxy.
- Production DNS is not changed during staging.
- No wildcard DNS route should expose unknown Salamandra hosts.
- DNS TTL is low during staging cutover and raised only after the route is stable.
- If CAA records are used, they permit only the chosen certificate authority.

Do not expose the app under a raw cloud hostname for real users unless that hostname is also in the explicit allowed-origin configuration and TLS certificate plan.

## 5. HTTPS And Reverse Proxy Boundary

Salamandra must terminate public HTTPS before traffic reaches the Python process. The reverse proxy or platform ingress must:

- Redirect HTTP to HTTPS.
- Present a valid certificate for the staging host.
- Restrict accepted Host headers to the staging host.
- Apply request body and header size limits compatible with `MAX_JSON_BODY_BYTES = 1_000_000` in `src/server.py:50`.
- Forward only to the app on a private network interface.
- Set operational timeouts so stuck requests do not exhaust threads.
- Preserve enough request metadata for logs without trusting arbitrary client-supplied forwarding headers.

The app currently adds HSTS only when `SALAMANDRA_COOKIE_SECURE=1` in `src/server.py:1454-1455`. That environment variable must be set for staging and production HTTPS. Do not preload HSTS until production hostnames and subdomains are stable.

## 6. Auth, Registration, Passwords, And Sessions

Current auth state:

- Password sign-in exists at `/api/auth/signin`.
- Demo sign-in exists but is disabled unless `SALAMANDRA_ENABLE_DEMO=1`.
- Logout revokes PostgreSQL sessions through `PostgresRuntime.revoke_session`.
- Sessions store only token hashes in the database.
- Existing sessions re-check `SessionModel.revoked_at`, `SessionModel.expires_at`, user status, and membership status on each request.
- Public registration, email verification, password reset, and invitation-token acceptance are not implemented.

Private staging account policy:

- Provision only named test users.
- Use a staging-only organization and staging-only data.
- Do not use real customer passwords.
- Do not enable demo mode.
- Require temporary passwords to be changed manually out of band until a first-login reset flow exists.

Application-security readiness:

- Session invalidation is acceptable for private staging and controlled pilot based on code inspection and local tests.
- Public self-service signup is not ready because there is no registration workflow, email verification, anti-abuse protection, or password reset flow.

## 7. CSRF, CORS, Cookies, And Browser Hardening

Current browser controls:

- Production-like modes require explicit exact origins and never derive trust from the request Host.
- Every production-like POST requires an approved Origin; credentialed CORS echoes only an exact approved origin and never `*`.
- Host headers are normalized and checked against an explicit allowlist.
- Session cookies include `HttpOnly`, `SameSite=Lax`, `Path=/`, bounded `Max-Age`, `Expires`, and mandatory `Secure` in staging/production.
- Logout mirrors the secure cookie attributes and revokes the server-side session.
- Proxy-provided client IPs are ignored unless proxy trust is enabled and the direct peer is an explicitly trusted IP.
- Security headers include CSP, frame denial, no-referrer, nosniff, permissions policy, and HSTS for secure deployments.

The complete variable inventory is in `config/staging.env.example`. Local development
keeps explicit localhost defaults, while staging and production fail closed.

## 8. Authorization And Tenant Isolation

Representative tenant/RBAC protections observed:

- Permissions are centralized in `src/security.py`.
- Route handlers call named permissions before sensitive actions.
- Event lookup in PostgreSQL includes authenticated organization context.
- `EventModel` uses same-organization owner constraints.
- `InventoryHoldingModel` uses same-organization owner constraints for personal holdings.
- `StockMovementModel`, `InventoryAdjustmentModel`, and `AuditEventModel` constrain actor/resource organization relationships.
- Personal inventory allocation includes shared holdings plus only the event owner's personal holdings in `src/database.py:804-825`.

Local evidence:

- `test_unsigned_state_does_not_expose_tenant_events`
- `test_org_a_cannot_read_org_b_event`
- `test_org_a_cannot_overwrite_org_b_event`
- `test_org_a_cannot_use_or_export_org_b_inventory`
- `test_technician_is_denied_every_administrative_or_shared_write_route`
- `test_reservation_rejects_another_users_personal_inventory`
- `test_database_rejects_cross_tenant_event_owner`

Tenant isolation survived this review for the inspected production core. Staging must still run cross-organization browser/API tests against the deployed site before real data enters the environment.

## 9. PostgreSQL Persistence, Schema, And Migration

Current persistence posture:

- `SALAMANDRA_DATABASE_URL` activates PostgreSQL runtime.
- `create_database_engine(..., production=True)` rejects non-PostgreSQL URLs.
- Alembic has migrations through `0004_inventory_ledger`.
- `migrations/env.py` reads `SALAMANDRA_DATABASE_URL` when present.
- Runtime model and clean SQLite migration test agree on core tables including `operation_requests` and `inventory_adjustments`.

Important schema elements:

- `OperationRequestModel` unique by `organization_id`, `operation`, `idempotency_key`.
- `StockMovementModel` unique by `organization_id`, `idempotency_key`.
- `InventoryAdjustmentModel` unique by `organization_id`, `operation`, `idempotency_key`, `holding_id`.
- Holdings have non-negative bucket constraints.
- Holdings have unique shared and personal item identities.
- Same-organization foreign keys protect membership-owned rows.

Required staging migration discipline:

- Apply Alembic migrations before starting the app.
- Confirm `alembic_version.version_num` equals repository head.
- Do not let the app start serving traffic on a stale schema.
- Rehearse downgrade only on disposable staging data. Production downgrade is a recovery procedure, not a routine deploy step.

## 10. Inventory, Allocation, Movement, And Transaction Integrity

The production transactional core is acceptable for a production pilot from an application data-integrity perspective, subject to staging evidence.

Confirmed by inspection:

- Normal reserve/pack/dispatch/return is inside `with self.factory.begin()` in `src/database.py:669-678`.
- `transition_in_session()` locks the event row with `with_for_update()` before validating state.
- Allocation movement locks allocation, allocation lines, and inventory holdings in deterministic order before bucket changes.
- Duplicate `out` and `returned` transitions return idempotently when the event is already in that status.
- Kit checkout creates operation request, event, allocation, packed state, dispatch movement, and audit in one `with self.factory.begin()` block.
- A failure before completion rolls back the operation request because it is created in the same transaction.
- Direct production stock use/return is disabled; stock quantity changes are event movements or inventory adjustments.

Local implementation evidence:

- 112 Python tests pass against disposable PostgreSQL with zero skips.
- PostgreSQL row-lock and two-process HTTP races cover reservation, inventory adjustment, kit checkout, dispatch, return, session invalidation, and readiness revision mismatch.
- TypeScript check and Vite production build pass with the bundled Node runtime.
- Alembic upgrade/downgrade/re-upgrade and `alembic check` pass against PostgreSQL.

Evidence still required before staging signoff:

- Repeat the HTTP smoke and race checks through the real staging ingress.
- Record TLS, proxy, access restriction, central logging/alerts, backup restore, browser, and rollback evidence.

## 11. Secrets And Integration Configuration

Current positive controls:

- Integration configuration stores server-selected credential environment variable names rather than raw secrets.
- CRM endpoint validation requires public HTTPS destinations and rejects local/private endpoints.
- Static bundle tests scan for known demo credentials.

Required deployment controls:

- Store `SALAMANDRA_DATABASE_URL` and integration secrets in the host/platform secret manager.
- Do not place secrets in `.env` files committed to the repo, browser state, docs JSON, or logs.
- Use a least-privilege PostgreSQL application role rather than a superuser.
- Use separate database users for migrations and runtime if the platform supports it.
- Rotate staging credentials before production pilot if they were shared during setup.

## 12. Logging, Monitoring, Health, And Audit

Current state:

- Structured JSON security/operations events include correlation ID, actor, organization, action, and result where available.
- Authentication success/failure/throttle, session invalidation/revocation, authorization denial, event/inventory conflicts, startup/shutdown, readiness failure, and unexpected errors are covered.
- Sensitive field names are filtered, and request bodies, passwords, cookies, tokens, database URLs, and integration credentials are not logged.
- Database audit rows remain authoritative for event transitions, kit checkout, and inventory adjustments.
- `/health` is unauthenticated liveness; `/ready` verifies database connectivity and exact Alembic head.
- Metrics, central collection, dashboards, and alert routing remain deployment-environment work.

Staging minimum:

- Reverse-proxy access logs with timestamp, route, status, duration, remote IP, and request ID.
- App logs for failed auth, authorization denial, state conflict, migration failure, and unexpected exception.
- A readiness probe that fails if PostgreSQL is unreachable or Alembic is not at head.
- Alerts for repeated 5xx, auth spikes, 403 spikes, database connection failures, and deadlocks.

Database audit rows are not a substitute for operational logs. They answer "what changed"; logs answer "what happened at the boundary."

## 13. Rate Limiting And Abuse Controls

Current rate limiting:

- Failed sign-in attempts are throttled in process memory by IP/email.
- The limit is not shared across multiple app processes.
- It depends on what the app sees as client address, which may be the proxy unless configured carefully.

Private staging minimum:

- Restrict staging to named testers, VPN, IP allowlist, basic auth at proxy, or equivalent.
- Add reverse-proxy rate limits for `/api/auth/signin`, `/api/auth/demo`, and all POST routes.
- Keep request body limits at proxy and app.
- Disable public registration until a proper registration and anti-abuse design exists.

Production pilot minimum:

- Centralized rate limiting or platform/WAF limits.
- Audit and alerts for credential stuffing patterns.
- Lockout/recovery procedure that cannot be abused to deny service to a whole organization.

## 14. CI/CD, Build, Release, And Rollback

CI implementation:

- PostgreSQL 16 is a required service and both database URL variables are supplied.
- Alembic upgrade/downgrade/re-upgrade and `alembic check` run before tests.
- The complete Python discovery suite includes PostgreSQL and multi-process race tests; CI cannot silently skip them because the test URL is present.
- pnpm 10 installs from `pnpm-lock.yaml` with `--frozen-lockfile` before TypeScript and production build checks.

Required release discipline:

- Fix CI package manager mismatch.
- CI must run Alembic upgrade, Python tests with real PostgreSQL, TypeScript check, production build, dependency audit, and secret scan.
- Deploy immutable artifacts.
- Run migrations before routing traffic.
- Keep a rollback artifact and a database recovery plan.
- Never use rollback as a substitute for restoring corrupted warehouse data from backups.

## 15. Findings And Required Gates

Implementation disposition for the findings below:

- Finding 1 HIGH: fixed in centralized production-like configuration and request validation.
- Finding 2 HIGH: fixed by `/health`, `/ready`, and startup Alembic revision gating.
- Finding 3 HIGH: deferred operational gate; backup/restore and real-data reconciliation require the staging environment and approved data copy.
- Finding 4 MEDIUM: fixed by pnpm-based CI.
- Finding 5 MEDIUM: deliberately retained for restricted staging; centralized proxy/WAF or persistent throttling remains a production-pilot gate.
- Finding 6 MEDIUM: restricted/manual/invite-only staging is documented; public lifecycle remains disabled and is a production-pilot gate.
- Finding 7 MEDIUM: minimum structured application logging fixed; external collection, alerting, and retention remain deployment work.
- Finding 8 LOW: fixed; secure logout cookie attributes mirror sign-in.
- Finding 9 LOW: deferred because inventory-definition audit expansion is outside this staging-hardening scope.

### Finding 1

Severity: HIGH

Location: `src/server.py:589-596`, `src/server.py:1423-1435`, `src/server.py:1454-1455`, `src/server.py:1461-1499`

Evidence: Secure cookies, HSTS, and explicit allowed origins are environment-dependent. If `SALAMANDRA_ALLOWED_ORIGINS` is not set, allowed origins are derived from the request Host header. Production PostgreSQL mode does not by itself require `SALAMANDRA_COOKIE_SECURE=1`, explicit origin allowlist, canonical host, or reverse-proxy Host validation.

Problem: A staging or pilot deployment can be started in PostgreSQL mode but still be exposed with weak browser/security-edge configuration.

Why it matters: A correct transactional core still loses confidentiality and session safety if deployed over HTTP, behind a permissive proxy, or with Host-derived origin validation.

Concrete failure scenario: The staging service is published at `https://salamandra-staging.hmimesh.com`, but `SALAMANDRA_COOKIE_SECURE` is missing and the backend is also reachable over HTTP. A session cookie can be sent over cleartext. Separately, a permissive proxy can forward a malicious Host header, causing origin validation to accept an attacker-controlled origin.

Recommended correction: For staging and production, fail startup unless a deployment mode requires HTTPS public origin, explicit `SALAMANDRA_ALLOWED_ORIGINS`, `SALAMANDRA_COOKIE_SECURE=1`, demo/dev switches disabled, and an explicit reverse-proxy Host allowlist. At minimum, enforce this in deployment scripts/runbook before DNS exposure.

Test that should demonstrate the correction: Start the app in deployment mode without each required variable and assert startup fails. Start with the staging origin and assert Set-Cookie includes `Secure`, HSTS is present, the expected origin is accepted, and a mismatched Origin plus forged Host is rejected.

### Finding 2

Severity: HIGH

Location: `src/server.py:75-117`, `src/server.py:1461-1499`, `migrations/env.py:24-56`

Evidence: No `/health/live` or `/health/ready` API route was found. Startup creates the runtime but does not check Alembic revision before serving.

Problem: Deployment automation cannot distinguish a live Python process from an app that is safe to receive traffic against the expected schema.

Why it matters: PostgreSQL schema drift is a real data-integrity risk. A process serving against a stale schema can fail mid-operation or leave operators with partial availability during a dispatch/return window.

Concrete failure scenario: A staging host starts after code including `InventoryAdjustmentModel` is deployed, but migration `0004_inventory_ledger` was not applied. The process accepts traffic, then `/api/inventory/items` fails when it attempts to write `inventory_adjustments`.

Recommended correction: Add liveness and readiness endpoints. Readiness should verify database connectivity, current Alembic revision equals repository head, and critical tables/columns exist. Deployment should apply migrations before starting or before flipping traffic.

Test that should demonstrate the correction: Start the app against a clean migrated DB and assert readiness passes. Start against a DB downgraded below head and assert readiness fails and deployment refuses traffic.

### Finding 3

Severity: HIGH

Location: Repository operations and deployment docs. Supporting risk is visible in `src/migrate_json_to_postgres.py` and migrations under `migrations/`.

Evidence: Migration tooling exists, but no completed backup/restore rehearsal or real warehouse reconciliation evidence is present in the working tree.

Problem: Production pilot cannot rely only on code tests. Real inventory/event data needs backup, migration, reconciliation, and restore proof.

Why it matters: Salamandra manages physical operational stock. A migration error that drops an in-use count, imports data into the wrong organization, or loses event history can cause incorrect packing, dispatch, or returns.

Concrete failure scenario: Real JSON data is migrated into PostgreSQL. A legacy item with `in_use_count` is mapped incorrectly, so the system shows stock as available and allocates it to a second event.

Recommended correction: Before pilot, run dry-run migration, full backup, restore to separate database, migration, reconciliation counts, representative operator validation, then record signoff. Keep original JSON immutable until pilot rollback window closes.

Test that should demonstrate the correction: On a copy of real data, assert per-organization counts, item IDs, available/reserved/packed/dispatched buckets, event statuses, active reservations, users, roles, item classes, and integration metadata match the approved reconciliation report. Restore the backup into a fresh DB and repeat the same checks.

### Finding 4

Severity: MEDIUM

Location: `.github/workflows/test.yml:39-42`, `package.json`, `pnpm-lock.yaml`

Evidence: CI uses npm cache and `npm ci` with `package-lock.json`, but the repository contains `pnpm-lock.yaml` and no `package-lock.json`.

Problem: CI frontend validation is likely broken or inconsistent with local dependency resolution.

Why it matters: A deployment pipeline that cannot reliably run frontend checks and builds can ship unreviewed static assets or block releases unexpectedly.

Concrete failure scenario: A pull request changes frontend API types. Local `pnpm` passes, but CI fails before TypeScript because `npm ci` has no lockfile, so reviewers either bypass CI or lose trust in the gate.

Recommended correction: Either switch CI to pnpm with `pnpm install --frozen-lockfile`, `pnpm run check`, and `pnpm run build`, or intentionally adopt npm and commit a matching `package-lock.json`.

Test that should demonstrate the correction: GitHub Actions runs on a clean checkout and passes Python, PostgreSQL, TypeScript, and Vite build steps.

### Finding 5

Severity: MEDIUM

Location: `src/server.py:546-568`, `src/server.py:1495-1497`

Evidence: Failed sign-in throttling is stored in `SalamandraServer.login_attempts`, which is process-local memory reset at startup.

Problem: Rate limiting is not durable or shared across app processes.

Why it matters: Private staging can compensate with access restriction, but public exposure or a production pilot needs reliable protection against credential stuffing and noisy login abuse.

Concrete failure scenario: Two app processes run behind a proxy. An attacker alternates requests between processes and avoids the five-failure threshold on either process.

Recommended correction: Put login and POST rate limits at the reverse proxy/WAF for staging. For production pilot, add a durable or managed rate-limit mechanism keyed by normalized email, client IP, and organization context where known.

Test that should demonstrate the correction: Send failed login attempts across two app processes and assert the combined threshold is enforced.

### Finding 6

Severity: MEDIUM

Location: `src/server.py:509-526`, `frontend/src/pages/TeamPage.tsx:41-42`, route list in `src/server.py:126-535`

Evidence: There is owner/admin team-member creation with a temporary password, but no public registration, invitation-token acceptance, first-owner bootstrap, email verification, password reset, or forced password change flow.

Problem: Account lifecycle is not production-complete.

Why it matters: Manual staging accounts are acceptable. Real customer onboarding and recovery are not safe without verified email ownership and a reset/change-password path.

Concrete failure scenario: A staging admin creates an account with a temporary password and sends it over chat. The user never changes it because the app has no first-login password change flow.

Recommended correction: For private staging, use named test users and out-of-band controls. Before production pilot, add or document secure first-owner provisioning, invitation acceptance, password reset, and forced temporary-password rotation.

Test that should demonstrate the correction: Invite flow should produce a single-use invitation token, require email ownership or controlled acceptance, force password set/change, and reject reused/expired tokens.

### Finding 7

Severity: MEDIUM

Location: `src/server.py:51`, `src/server.py:542-544`, `src/server.py:1457-1458`

Evidence: Default request logs are suppressed. Unexpected exceptions are logged, but no structured security or request log format is present.

Problem: The system lacks enough operational/security telemetry for staging and pilot incident review.

Why it matters: Tenant isolation, auth failures, migration issues, and transaction conflicts need observable evidence during pilot operations.

Concrete failure scenario: A user reports seeing the wrong event list. Without request ID, actor membership ID, organization ID, route, status, and timing logs, it is hard to determine whether this was a UI cache issue, auth issue, or cross-tenant access attempt.

Recommended correction: Add structured logs at the API boundary and emit security events for auth success/failure, logout, authorization denial, CSRF/origin denial, idempotency conflicts, transaction conflicts, and unexpected exceptions. Redact request bodies and secrets.

Test that should demonstrate the correction: Exercise login failure, cross-origin rejection, 403, 409, and 500 paths and assert structured logs include route, status, request ID, actor/org when known, and no password/session token.

### Finding 8

Severity: LOW

Location: `src/server.py:607-613`

Evidence: Login Set-Cookie conditionally includes `Secure`, but logout cookie clearing does not mirror the `Secure` attribute.

Problem: Cookie clearing should match production cookie attributes exactly.

Why it matters: Most browsers clear by name/domain/path, but exact attribute parity reduces browser/proxy edge-case risk and makes tests clearer.

Concrete failure scenario: A staging browser keeps an old secure session cookie after logout due to attribute handling differences, and the next request still appears authenticated until server revocation is observed.

Recommended correction: Include the same `Secure` behavior on the logout clearing cookie when `SALAMANDRA_COOKIE_SECURE=1`.

Test that should demonstrate the correction: With secure-cookie mode enabled, login and logout response headers both include the expected cookie attributes, and the old cookie is unusable server-side.

### Finding 9

Severity: LOW

Location: `src/postgres_runtime.py:335-348`

Evidence: Inventory item metadata/name update changes holding identity/data without writing an audit event. It does not change quantity buckets.

Problem: Non-quantity inventory edits are less traceable than quantity adjustments.

Why it matters: Operators may need to understand who renamed an item or changed metadata that affects planning and packing labels.

Concrete failure scenario: A shared item is renamed before an event. Quantities remain correct, but audit history does not show who changed the operational label.

Recommended correction: Add an audit event for inventory metadata/name edits.

Test that should demonstrate the correction: Update an inventory item name/metadata and assert an `inventory.definition_updated` audit row is written with organization, actor membership, old ID, and new ID.

## Acceptance Summary

PostgreSQL transactional core acceptable for production pilot: Yes, for a controlled pilot after staging evidence and operational blockers are closed.

Tenant isolation survived final review: Yes, for the inspected production runtime and representative tests.

Stock movements protected against duplicate/concurrent mutation: Yes for inspected dispatch/return/kit/inventory adjustment paths, with real PostgreSQL staging race tests still required as a release gate.

Authentication/session invalidation acceptable: Yes for private staging and controlled pilot. Public account lifecycle remains incomplete.

Remaining before deployment:

- Private staging DNS, TLS, reverse proxy, access restriction, and environment configuration evidence.
- Staging PostgreSQL least-privilege roles plus backup/restore rehearsal.
- Central log collection, monitoring, and alerts.
- Staging browser smoke and application rollback rehearsal.
- Real data migration/reconciliation signoff before production pilot.
