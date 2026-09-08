# Salamandra Staging Checklist

Use this checklist for the first private staging deployment and repeat it after every deployment candidate until Salamandra has a stable release process.

Do not use production warehouse data unless every required item is PASS and the production pilot gate is explicitly approved.

Target: `https://salamandra-staging.hmimesh.com`

## First Private Staging Execution Order

Execute this checklist in the following order; do not route named testers until
the infrastructure and account gates pass:

1. Record the immutable release commit and hosting environment below.
2. Complete Sections 1-5 for fail-closed startup, PostgreSQL, DNS, TLS, cookies,
   trusted Host, and origin enforcement.
3. Provision only the Org A owner, Org A technician/operator, and Org B owner test
   identities, then complete Sections 6-14.
4. Open three isolated browser profiles, one for each identity.
5. Complete Sections 15-24 using fake events and fake inventory.
6. Complete the restart proof in Section 25 without restarting PostgreSQL.
7. Complete migration, backup, observability, browser, and rollback evidence in
   Sections 26-30.

Do not paste passwords, cookies, database URLs, or tokens into this document.

## Reproducible Browser QA

The browser suite uses Playwright Chromium against an isolated local Salamandra
server at `http://127.0.0.1:4173`. It never uses staging credentials or staging
data. Prerequisites: Python 3.12 with `requirements.txt` installed, Node 22+,
pnpm, and an unused port 4173. On Windows the runner uses `.venv/Scripts/python.exe`;
on other platforms it uses `python` from PATH. From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
pnpm install
pnpm exec playwright install chromium
pnpm qa:browser
```

The command builds the production frontend before starting the disposable test
server. Automated coverage includes 360px mobile, 768px tablet, 1280px desktop,
and 1440px wide layouts. A fifth project emulates a 1280x900 browser at 200% zoom
using 640x450 CSS pixels and device scale factor 2. Every project runs all ten
scenarios (50 tests, no intentional skips): navigation links, focus trapping and
restoration, Escape handling, manual planning and crew assignment, kit creation,
preset and custom inventory, dependency selection, account popovers, calendar
overflow, cancellation/delete confirmations, CSV mapping and import, and appearance.
Every test checks console/page errors; key states check document overflow.
The multilingual scenario checks original inventory display text, Unicode search,
stable IDs during display-name edits, and RTL briefs. CSV QA rejects invalid UTF-8.
See [Phase A](MULTILINGUAL_PHASE_A.md) for catalog mappings and migration boundaries.

Compact/default/large/largest text is exercised at every viewport, with light/dark
coverage. Compact text retains a computed >=14px floor for calendar events, manual
planner labels, dependency rows, kit labels, CSV mapping/preview, and operational
facts/status. Decorative text is unchanged. Compact density still reduces spacing:
the suite compares settings-grid gaps at the same text size and checks an
at-least-4px reduction, dialog Escape behavior, control bounds, and viewport overflow.
Zoom emulation tests reflow and pixel scaling; native browser chrome zoom and
non-Chromium browsers still require the manual staging check. Screenshots and
failure traces are written under ignored `test-results/`; CI uploads these along
with its Playwright report. No pixel-perfect screenshot baseline is required.

Playwright global setup owns the Python fixture directly (no shell). The parent
stdin pipe is its lifetime: normal teardown and runner exit close it, and the
fixture shuts down its HTTP server, joins its worker, closes its socket, and removes
its temporary data. Teardown waits up to 10 seconds for child exit and verifies that
port 4173 is closed; timeout or abnormal exit fails QA. It refuses an occupied port.
This also runs after test failures, preserving a nonzero test result. The Python
suite independently tests pipe-EOF shutdown and port release. No taskkill is used.

The separate `Windows browser QA lifecycle` CI job runs the same command with an
8-minute step timeout and a 15-minute job timeout, then independently checks that
neither a Python fixture process nor a port-4173 listener remains. The existing
Linux/PostgreSQL production-core job is unchanged in strictness.

### Dependency Target Lifecycle Acceptance

PostgreSQL commands reject rename or removal-to-zero/archive with HTTP 409 when
any active same-organization definition resolves a dependency to that target.
This includes other users' personal definitions depending on shared stock. Personal
edges still resolve only within their owner's inventory or shared inventory, never
another user's personal stock. References are not silently rewritten or redirected
to a fallback item. Unreferenced definitions may be renamed or archived normally.

The existing organization row lock covers complete post-operation graph validation
and the full command transaction. Creation, definition updates, reactivation, and
CSV reconciliation validate all active organization definitions, not just updated
roots. Archive/removal and rename reject incoming references before mutation.
Failure rolls back holdings, metadata, adjustments, operation requests, and audits.
Two independent HTTP processes race target removal/rename against a dependency
update: exactly one succeeds and the committed graph remains valid. No migration
or event/allocation architecture change is involved.

The test server uses disposable local compatibility stores and fake accounts,
not the production database. PostgreSQL authorization, transactions, and races
are independently required by `python tests/run_production_core_acceptance.py`
with `SALAMANDRA_TEST_POSTGRES_URL` and `SALAMANDRA_REQUIRE_POSTGRES_TESTS=1`.
Do not use the browser fixture server as a deployment entrypoint.

## Code Gate Before Environment Testing

Confirm the release candidate includes all of these before beginning the checklist:

- [ ] Production-like configuration fails closed on missing/insecure origin, Host, cookie, database, proxy, demo, or JSON settings.
- [ ] `/health` and `/ready` exist; startup and readiness require the current Alembic head.
- [ ] CI uses pnpm's frozen lockfile and supplies a real PostgreSQL service URL to the complete Python suite.
- [ ] Structured application logs are enabled and redact secrets.
- [ ] `config/staging.env.example` and `docs/STAGING_RUNBOOK.md` match the release candidate.

These are code-presence checks, not deployment approval. Every environment result below still needs evidence.

## Environment Record

- Date:
- Release/source commit:
- Tester:
- Staging URL:
- App host:
- PostgreSQL host/database:
- Alembic head revision:
- Reverse proxy/ingress:
- Browser set:
- Org A owner profile:
- Org A lower-role profile and role:
- Org B owner profile:
- Notes:

## Required Result Format

For every check, mark one result:

- [ ] PASS
- [ ] FAIL

Attach logs, screenshots, command output, or database queries as evidence where practical.

## 1. Production Mode Fails Closed Without PostgreSQL

Result:

- [ ] PASS
- [ ] FAIL

Steps:

1. Set `SALAMANDRA_ENV=staging` and the required origin/Host/cookie variables.
2. Start the app with `SALAMANDRA_DATABASE_URL` unset.
3. Ensure `SALAMANDRA_ALLOW_JSON_DEV` and `SALAMANDRA_ENABLE_DEMO` are unset.

Expected:

- App exits before serving traffic.
- Error identifies invalid deployment configuration without printing a database URL or secret.
- No JSON files are modified.

Evidence:

- Command:
- Output:

## 2. Staging Starts Only With PostgreSQL

Result:

- [ ] PASS
- [ ] FAIL

Steps:

1. Set `SALAMANDRA_DATABASE_URL` to the staging PostgreSQL URL.
2. Run `alembic upgrade head`.
3. Start the app behind the staging reverse proxy.
4. Confirm JSON development flags are not set.

Expected:

- App starts and uses `PostgresRuntime`.
- `SALAMANDRA_ALLOW_JSON_DEV` and `SALAMANDRA_ENABLE_DEMO` are absent or false.
- `docs/inventory.json`, `docs/inventories.json`, `docs/events.json`, `docs/users.json`, `docs/item_classes.json`, and `docs/integrations.json` hashes do not change during API mutations.

Evidence:

- Environment snapshot with secrets redacted:
- JSON hash before/after:

## 3. DNS And HTTPS

Result:

- [ ] PASS
- [ ] FAIL

Steps:

1. Resolve `salamandra-staging.hmimesh.com`.
2. Run `python scripts/staging_public_smoke.py` from the release checkout.
3. Open `https://salamandra-staging.hmimesh.com`.
4. Attempt plain HTTP.
5. Attempt the app through any raw host/IP that should not be public.

Expected:

- Staging DNS points only to staging ingress.
- HTTPS certificate is valid for the staging host.
- HTTP redirects to HTTPS or is blocked.
- Raw host/IP access is blocked or not routed.
- Production DNS is unchanged.

Evidence:

- DNS output:
- TLS/certificate output:

## 4. Cookie And Security Headers

Result:

- [ ] PASS
- [ ] FAIL

Steps:

1. Sign in to staging.
2. Inspect the sign-in response headers.
3. Inspect a normal API response and a static asset response.

Expected:

- `Set-Cookie` contains `HttpOnly`, `SameSite=Lax`, `Secure`, `Path=/`, and a bounded `Max-Age`.
- HSTS is present on HTTPS responses when `SALAMANDRA_COOKIE_SECURE=1`.
- CSP, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, and `Permissions-Policy` are present.
- API responses use `Cache-Control: no-store`.

Evidence:

- Response headers:

## 5. CSRF And Origin Rejection

Result:

- [ ] PASS
- [ ] FAIL

Steps:

1. Send a valid same-origin POST mutation.
2. Send the same mutation with `Origin: https://evil.example`.
3. Send with a forged Host header if the proxy permits the test.

Expected:

- Same-origin POST succeeds when authorized.
- Mismatched origin receives HTTP 403.
- Forged Host does not make the backend accept an unapproved origin.
- A `404 Application not found` is also an acceptable result for this forged-Host
  probe only: Railway may reject the unknown Host at its routing edge before the
  Salamandra application receives the request. Do not accept `404` as success for
  health, readiness, authentication, or any other smoke check.
- `SALAMANDRA_ALLOWED_ORIGINS` is explicitly set to the staging origin.

Evidence:

- Requests and statuses:

## 6. Registration And Account Provisioning

Result:

- [ ] PASS
- [ ] FAIL

Steps:

1. With `SALAMANDRA_REGISTRATION_MODE=open`, create one empty test workspace at `/register`.
2. Retry the same normalized email and confirm that no second workspace is created.
3. Set registration to `disabled`, restart, and verify that `/register` cannot create a workspace.
4. Attempt demo sign-in.
5. As owner/admin, create a named staging tester through team management.
6. As a non-admin role, attempt the same action directly against `/api/team/invite`.

Expected:

- Open registration works only when explicitly configured and creates an empty workspace.
- Disabled or malformed registration configuration fails closed.
- Demo sign-in is unavailable.
- Owner/admin can create a staging account in the same organization.
- Lower roles receive HTTP 403 for account creation.
- Temporary-password handling is recorded as a staging limitation.

Evidence:

- Endpoints/statuses:

## 7. Login Happy Path

Result:

- [ ] PASS
- [ ] FAIL

Steps:

1. Sign in with a valid staging user.
2. Load `/api/state`.
3. Refresh the browser.

Expected:

- Login succeeds.
- State is authenticated.
- User, role, organization, and warehouse match the database.
- No password hash, session token, database URL, or secret appears in the response.

Evidence:

- User/org shown:
- Redaction check:

## 8. Login Failure And Rate Limiting

Result:

- [ ] PASS
- [ ] FAIL

Steps:

1. Attempt sign-in with a wrong password repeatedly.
2. Repeat through the public staging route, not just localhost.
3. If multiple app processes are deployed, alternate requests across them.

Expected:

- Error is generic.
- Rate limit triggers.
- Proxy/WAF rate limit works across app processes.
- Logs record failures without passwords.

Evidence:

- Status sequence:
- Relevant redacted log lines:

## 9. Logout Revocation

Result:

- [ ] PASS
- [ ] FAIL

Steps:

1. Sign in and capture the session cookie.
2. Call `/api/auth/signout`.
3. Reuse the old cookie on `/api/state` and one protected POST route.

Expected:

- Logout returns unauthenticated state.
- Old cookie is rejected server-side immediately.
- Protected POST receives HTTP 401.
- Logout response clears the cookie.

Evidence:

- Cookie and status results:

## 10. Session Persistence Across Restart

Result:

- [ ] PASS
- [ ] FAIL

Steps:

1. Sign in.
2. Restart the app process without changing the database.
3. Reuse the original cookie.

Expected:

- Authenticated session survives process restart.
- State is identical for user/org/event data.
- No JSON file changes are required for session persistence.

Evidence:

- Before/after status:

## 11. Session Invalidation On Disabled User

Result:

- [ ] PASS
- [ ] FAIL

Steps:

1. Sign in as a staging user.
2. Disable that user directly in PostgreSQL or through an approved admin path.
3. Reuse the original cookie.

Expected:

- `/api/state` reports unauthenticated state.
- Protected mutations receive HTTP 401.
- No stale in-memory account state keeps access alive.

Evidence:

- SQL/admin action:
- Status results:

## 12. Session Invalidation On Disabled Membership

Result:

- [ ] PASS
- [ ] FAIL

Steps:

1. Sign in as a staging user.
2. Disable that user's membership.
3. Reuse the original cookie.

Expected:

- User becomes unauthenticated immediately.
- Organization-scoped reads and writes are unavailable.

Evidence:

- SQL/admin action:
- Status results:

## 13. Session Expiry And Manual Revocation

Result:

- [ ] PASS
- [ ] FAIL

Steps:

1. Sign in and capture the session row.
2. Set `expires_at` in the past.
3. Reuse the cookie.
4. Sign in again, set `revoked_at`, and reuse that cookie.

Expected:

- Expired session is rejected.
- Revoked session is rejected.
- No process restart is needed for invalidation.

Evidence:

- SQL actions:
- Status results:

## 14. Role Permissions

Result:

- [ ] PASS
- [ ] FAIL

Steps:

1. Create users for owner/admin/operator/technician/freelancer/client or the available staging subset.
2. Directly call admin/shared-inventory/item-class/integration/team endpoints from lower roles.
3. Attempt UI and direct HTTP paths.

Expected:

- Lower roles cannot invoke administrator endpoints directly.
- Technicians/freelancers can write only their own personal inventory where permitted.
- Clients/read-only users cannot mutate inventory/events.

Evidence:

- Role matrix and statuses:

## 15. Org A Cannot Read Org B

Result:

- [ ] PASS
- [ ] FAIL

Steps:

1. Create Organization A and Organization B fixtures.
2. Sign in as Org A.
3. Attempt to read Org B event IDs, inventory exports, item classes, integrations, and team data.

Expected:

- Org B resources are absent or return non-disclosing not found.
- Org B names, item IDs, event titles, and users are not present in Org A API responses or CSV exports.

Evidence:

- Requests and redacted responses:

## 16. Org A Cannot Modify Org B

Result:

- [ ] PASS
- [ ] FAIL

Steps:

1. Sign in as Org A.
2. Submit Org B IDs in JSON bodies for checklist update, status transition, inventory update/remove, item class remove, integrations, and team paths.
3. Submit Org B's real event ID and a nonexistent event ID separately to `/api/events/save` as client-selected creation IDs.

Expected:

- Organization-scoped update commands fail with 403 or non-disclosing 404.
- Both `/api/events/save` probes return the same generic 400 response without revealing whether the ID exists.
- Org B rows remain unchanged.
- Org A event, allocation, inventory, movement, audit, and idempotency rows remain unchanged by both rejected creation requests.
- Audit logs do not claim successful Org A mutation of Org B resources.

Evidence:

- Before/after database checks:

## 17. Forged Event And Allocation Data

Result:

- [ ] PASS
- [ ] FAIL

Steps:

1. POST `/api/events/save` with a client-declared event ID, organization ID, owner ID, status `out`, forged allocation plan, forged movements, and nonexistent inventory.
2. Repeat without a client-selected ID but retain the forged server-owned fields.
3. POST `/api/events/status` with illegal transitions, for example planning directly to out.

Expected:

- Any client-selected creation ID receives the same generic HTTP 400 response and causes no mutation.
- ID-less creation generates the ID server-side and derives organization, owner, assigned users, planning status, verified plan, allocation, and movement fields.
- Illegal transitions fail.
- Nonexistent/forged stock is not persisted or moved.

Evidence:

- Request payload:
- Resulting database rows:

## 18. Kit Checkout Idempotency And Rollback

Result:

- [ ] PASS
- [ ] FAIL

Steps:

1. Seed a stocked kit in the staging test organization.
2. Send two identical concurrent `/api/kits/checkout` requests with the same idempotency key through two API processes if available.
3. Reuse the same key with a different source kit.
4. Attempt checkout for a kit with missing required stock.

Expected:

- Identical race creates exactly one operation request, one event, one allocation, one kit checkout audit, and one dispatch effect.
- Conflicting source/body receives HTTP 409.
- Missing-stock failure leaves no operation request, event, allocation, movement, audit, or stock change.

Evidence:

- HTTP statuses:
- Counts for operation_requests/events/allocations/stock_movements/audit_events:
- Holding bucket before/after:

## 19. Normal Event Dispatch Race

Result:

- [ ] PASS
- [ ] FAIL

Steps:

1. Create a confirmed/packed event with real allocated stock.
2. From two separate API processes, dispatch the same event at the same time.
3. Query holdings, allocation lines, event status, and stock movements.

Expected:

- Both requests are safe: one performs the transition, retry is idempotent.
- Exactly one `out` movement exists for that event.
- Available/reserved/packed/dispatched quantities are conserved and non-negative.

Evidence:

- HTTP statuses:
- Database counts and quantities:

## 20. Normal Event Return Race

Result:

- [ ] PASS
- [ ] FAIL

Steps:

1. Create or use a dispatched event with completed return checklist.
2. From two separate API processes, return the same event at the same time.
3. Query holdings, allocation lines, event status, and stock movements.

Expected:

- Both requests are safe: one performs the transition, retry is idempotent.
- Exactly one `returned` movement exists for that event.
- Inventory is restored exactly once.

Evidence:

- HTTP statuses:
- Database counts and quantities:

## 21. Direct Inventory Movement Bypass

Result:

- [ ] PASS
- [ ] FAIL

Steps:

1. In PostgreSQL mode, call `/api/inventory/use`.
2. Call `/api/inventory/return`.
3. Search/try any equivalent legacy endpoint or UI action that directly moves available/dispatched quantities.

Expected:

- Direct use and return receive HTTP 409.
- No `inventory_holdings` buckets change.
- No unauthorized `stock_movements` are created.
- Legitimate inventory add/remove/import creates `inventory_adjustments` and audit rows.

Evidence:

- Status results:
- Holding and ledger counts before/after:

## 22. Personal Inventory Ownership

Result:

- [ ] PASS
- [ ] FAIL

Steps:

1. Give one technician personal inventory.
2. Create an event owned by another user requiring that item.
3. Attempt confirmation/allocation.
4. Repeat with the event owner's personal item.

Expected:

- Other member's personal inventory is not sourced.
- Event owner's personal inventory may be sourced when authorized.
- Shared inventory remains available to the organization.

Evidence:

- Allocation rows:
- Holding buckets:

## 23. Malformed API Requests

Result:

- [ ] PASS
- [ ] FAIL

Steps:

1. Send non-object JSON.
2. Send malformed identifiers, overlong identifiers, negative/zero quantities, duplicate CSV IDs, oversized rows, and unsupported states.
3. Repeat against important event/inventory routes.

Expected:

- Requests fail cleanly with 400/403/404/409/413 as appropriate.
- No partial event, allocation, movement, adjustment, or audit rows are created on rejected requests.
- No traceback, secret, database URL, or password hash is returned.

Evidence:

- Payload/status matrix:
- Database before/after counts:

## 24. CSV Import And Export Safety

Result:

- [ ] PASS
- [ ] FAIL

Steps:

1. Import valid CSV into shared inventory.
2. Import CSV with a valid first row and invalid later row.
3. Import duplicate IDs.
4. Export inventory containing spreadsheet-formula-looking values.
5. Attempt Org A export after seeding Org B-only items.

Expected:

- Valid import writes ledger/audit rows.
- Invalid import is atomic and leaves no partial rows.
- Duplicate IDs are rejected.
- Export neutralizes spreadsheet formula prefixes.
- Export is organization-scoped.

Evidence:

- CSV samples:
- Ledger/audit counts:
- Export excerpt:

## 25. Database Persistence And Restart Behavior

Result:

- [ ] PASS
- [ ] FAIL

Steps:

1. Create inventory, event, checklist updates, dispatch, return, item class, integration config, profile, and preferences.
2. Record the active session, event IDs, holding buckets, movement counts, and `/ready` response.
3. Remove the app from traffic and stop only the application process.
4. Keep PostgreSQL running and restart the same immutable application artifact.
5. Wait for `/ready`, then reuse the original cookie and re-read state and database rows.
6. Repeat with logged-out, expired, revoked, disabled-user, and disabled-membership cookies.

Expected:

- Data persists in PostgreSQL.
- Session persists unless logged out/expired/revoked.
- Disabled users and disabled memberships remain unauthenticated after restart.
- JSON persistence file hashes are unchanged.
- No state is stored only in process memory except transient rate-limit counters.

Evidence:

- Before/after state:
- JSON hashes:

## 26. Alembic Clean Database And Downgrade Rehearsal

Result:

- [ ] PASS
- [ ] FAIL

Steps:

1. Create a disposable staging database.
2. Run `alembic upgrade head`.
3. Confirm tables, indexes, constraints, and Alembic version.
4. Run downgrade/re-upgrade only on disposable data.

Expected:

- Upgrade succeeds on clean PostgreSQL.
- Runtime can start and pass smoke tests after upgrade.
- Downgrade/re-upgrade rehearsal succeeds or documented irreversible migration policy is approved.

Evidence:

- Alembic output:
- Schema checks:

## 27. Backup And Restore Rehearsal

Result:

- [ ] PASS
- [ ] FAIL

Steps:

1. Configure encrypted staging PostgreSQL backups.
2. Take a manual backup after seeding staging data.
3. Restore into a separate database.
4. Point a disposable app instance at the restored DB.
5. Run core smoke and reconciliation queries.

Expected:

- Restore completes within the target RTO.
- Restored data matches expected counts and operational state.
- Operators know where backups live and who can restore.
- Backup credentials are not exposed to app users or logs.

Evidence:

- Backup ID:
- Restore duration:
- Reconciliation counts:

## 28. Observability And Audit Evidence

Result:

- [ ] PASS
- [ ] FAIL

Steps:

1. Exercise login success/failure, logout, 403, 404, 409, 413, 500 test path if available, dispatch, return, kit checkout, inventory adjustment, import/export.
2. Inspect application logs, proxy logs, database audit rows, and alerts.

Expected:

- Logs include route, status, duration, request ID, actor/org when known, and redacted error context.
- No passwords, session cookies, DB URLs, API keys, or OAuth secrets appear.
- Alerts fire for configured severe conditions.
- Audit rows explain successful operational/data changes.

Evidence:

- Redacted log samples:
- Audit row samples:

## 29. Browser Smoke

Result:

- [ ] PASS
- [ ] FAIL

Steps:

1. Open separate browser profiles or isolated contexts for Org A owner, Org A lower-role user, and Org B owner.
2. Test latest Chrome, Edge, Firefox, and Safari if available.
3. Test desktop and a mobile viewport.
4. Exercise login, navigation, inventory, event create, kit checkout, checklist, dispatch, return, settings, team, import, and export.
5. While Org A owner edits an event/checklist, have the Org A lower-role user attempt a representative restricted operation and have Org B owner create or edit an Org B event.
6. Refresh all three sessions and confirm each remains in the correct organization with no cross-tenant state.

Expected:

- No console errors on critical paths.
- Layout is usable at tested viewports.
- Downloads work.
- Auth redirects behave correctly after logout/session invalidation.
- Simultaneous operations conserve inventory and preserve role and tenant boundaries.

Evidence:

- Browser/version matrix:
- Screenshots:

## 30. Rollback And Incident Procedure

Result:

- [ ] PASS
- [ ] FAIL

Steps:

1. Identify previous deployable artifact.
2. Rehearse app rollback on staging.
3. Rehearse database restore to a separate database.
4. Document who approves rollback during an event operations window.

Expected:

- App rollback is understood and timed.
- Database recovery path is tested without overwriting live data accidentally.
- Operators know when to freeze dispatch/returns during incident handling.

Evidence:

- Rollback drill output:
- Approval/contact list:

## Final Gate

Private staging may open to named testers only when all of these are PASS:

- Sections 1 through 5
- Sections 6 through 13
- Sections 14 through 25
- Section 26 upgrade path
- Section 27 at least one restore rehearsal for staging
- Section 28 baseline logs and audit evidence
- Section 29 browser smoke on the agreed browser set

Production pilot may begin only after private staging passes and the real warehouse-data migration/reconciliation, backup/restore, monitoring, rollback, and account-lifecycle decisions are signed off.
