# Salamandra Staging Checklist

Use this checklist for the first private staging deployment and repeat it after every deployment candidate until Salamandra has a stable release process.

Do not use production warehouse data unless every required item is PASS and the production pilot gate is explicitly approved.

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
2. Open `https://salamandra-staging.hmimesh.com`.
3. Attempt plain HTTP.
4. Attempt the app through any raw host/IP that should not be public.

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
- `SALAMANDRA_ALLOWED_ORIGINS` is explicitly set to the staging origin.

Evidence:

- Requests and statuses:

## 6. Registration And Account Provisioning

Result:

- [ ] PASS
- [ ] FAIL

Steps:

1. Attempt to find or invoke a public registration endpoint.
2. Attempt demo sign-in.
3. As owner/admin, create a named staging tester through team management.
4. As a non-admin role, attempt the same action directly against `/api/team/invite`.

Expected:

- Public registration is unavailable for private staging.
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
2. Submit Org B IDs in JSON bodies for event save, checklist update, status transition, inventory update/remove, item class remove, integrations, and team paths.

Expected:

- Cross-organization mutation fails with 403 or non-disclosing 404.
- Org B rows remain unchanged.
- Audit logs do not claim successful Org A mutation of Org B resources.

Evidence:

- Before/after database checks:

## 17. Forged Event And Allocation Data

Result:

- [ ] PASS
- [ ] FAIL

Steps:

1. POST `/api/events/save` with a client-declared event ID, organization ID, owner ID, status `out`, forged allocation plan, forged movements, and nonexistent inventory.
2. POST `/api/events/status` with illegal transitions, for example planning directly to out.

Expected:

- Server derives ID, org, owner, assigned users, status, plan verification, allocation, and movement fields.
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
2. Restart the app.
3. Re-read state and database rows.

Expected:

- Data persists in PostgreSQL.
- Session persists unless logged out/expired/revoked.
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

1. Test latest Chrome, Edge, Firefox, and Safari if available.
2. Test desktop and a mobile viewport.
3. Exercise login, navigation, inventory, event create, kit checkout, checklist, dispatch, return, settings, team, import, and export.

Expected:

- No console errors on critical paths.
- Layout is usable at tested viewports.
- Downloads work.
- Auth redirects behave correctly after logout/session invalidation.

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
