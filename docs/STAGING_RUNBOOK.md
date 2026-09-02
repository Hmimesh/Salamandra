# Salamandra Private Staging Runbook

This runbook covers a restricted, named-tester deployment. It does not authorize
production data, public registration, DNS changes, or a production pilot.

## 1. Required Configuration

Load configuration from the deployment platform or secret manager using
`config/staging.env.example` as the variable inventory. Never commit a completed
environment file.

Required staging posture:

- `SALAMANDRA_ENV` is `staging`.
- `SALAMANDRA_DATABASE_URL` is a PostgreSQL URL held as a secret.
- `SALAMANDRA_APP_ORIGIN` is the exact public HTTPS origin.
- `SALAMANDRA_ALLOWED_ORIGINS` contains only approved exact HTTPS origins.
- `SALAMANDRA_TRUSTED_HOSTS` contains the public application hostname.
- `SALAMANDRA_COOKIE_SECURE` is enabled.
- Demo and JSON compatibility modes are disabled.
- Proxy headers remain disabled unless every direct proxy IP is explicitly listed.

The application fails startup when a production-like configuration is missing or
insecure. It does not fall back to localhost, demo credentials, SQLite, or JSON.

## 2. Database Creation

Create a dedicated staging PostgreSQL database and a least-privilege application
role. Use a separate migration role when the hosting environment supports it.
Enable encrypted backups before entering any operational test data. Record the
database owner, backup location, retention, and restore operator outside this repo.

## 3. Migrations

From the release checkout, with `SALAMANDRA_DATABASE_URL` loaded:

```powershell
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\alembic.exe current
.\.venv\Scripts\alembic.exe check
```

Deployment order is always:

```text
migration -> application start -> /ready becomes healthy -> route tester traffic
```

The application does not run hidden schema migrations. Startup and `/ready` reject
a database whose Alembic revision is not exactly the repository head.

## 4. Application Startup

Run the Python service under a process supervisor behind an HTTPS reverse proxy or
managed ingress. Do not expose `ThreadingHTTPServer` directly as the internet edge.

```powershell
.\.venv\Scripts\python.exe src/server.py
```

The edge must restrict Host values, redirect or block plain HTTP, apply request
limits, and forward only to the private application listener. Trust forwarded
client addresses only when the direct proxy IP matches
`SALAMANDRA_TRUSTED_PROXY_IPS`.

## 5. Readiness Verification

Before routing tester traffic:

```powershell
curl.exe --fail-with-body https://salamandra-staging.hmimesh.com/health
curl.exe --fail-with-body https://salamandra-staging.hmimesh.com/ready
```

`/health` proves only that the process is alive. `/ready` returns `200` only when
PostgreSQL is reachable and the schema is at the expected Alembic revision. A `503`
keeps the instance out of service. Neither endpoint returns database details,
credentials, tenant information, or schema identifiers.

## 6. First Admin And Accounts

Create the first owner from a controlled terminal after migrations:

```powershell
.\.venv\Scripts\python.exe src/provision_admin.py `
  --organization-id <staging-organization-id> `
  --organization-name <staging-organization-name> `
  --email <named-owner-email> `
  --name <named-owner-name>
```

The command reads the temporary password without echo and never accepts it as a
command-line argument. Additional named users may be created by an owner/admin
through team management.

Private staging is manual and invite-only. Public registration, demo sign-in,
email verification, password reset, invitation-token acceptance, and public SaaS
onboarding are disabled or absent. Share temporary passwords out of band and do
not reuse real customer passwords. A secure password-change/reset lifecycle is a
production-pilot blocker.

## 7. Smoke Tests

Complete `docs/STAGING_CHECKLIST.md`. At minimum verify:

- valid sign-in, durable session after app restart, logout, expiry, and disabled-user invalidation
- exact-origin CORS, rejected missing/untrusted Origin, and rejected untrusted Host
- secure cookie attributes and security headers
- organization isolation and lower-role API denial
- event reserve, pack, dispatch, return, and kit checkout against PostgreSQL
- concurrent dispatch/return/kit retries through two app processes
- unchanged hashes for all JSON compatibility files
- redacted structured logs and matching database audit/movement records

Login throttling is currently process-local. For private staging, restrict access
to named testers and enforce an additional proxy/WAF sign-in limit. Shared,
persistent throttling is required before a production pilot with multiple app
processes. Redis is not required for this staging phase.

## 8. Shutdown And Restart

Remove the instance from traffic, allow in-flight requests to finish, then stop it
through the process supervisor. Do not kill the database during active inventory
movements. After restart, wait for `/ready` before restoring traffic and verify an
existing session plus one inventory/event read. Sessions and application state are
PostgreSQL-backed and should survive an app-process restart.

## 9. Application Rollback

Keep the previous immutable application artifact. If the new application fails:

1. Remove the new instance from traffic.
2. Preserve logs and record the correlation IDs and release identifier.
3. Re-route to the previous artifact only if its expected schema is compatible.
4. Verify `/ready`, sign-in, inventory reads, and an event read before reopening.
5. If data integrity may be affected, freeze inventory movements and reconcile the
   ledger before allowing dispatch or return.

Do not blindly run `alembic downgrade` on staging data. Rehearse downgrade only on
a disposable database. For an incompatible or corrupting migration, use the
approved database recovery plan and a tested backup restore into a separate
database, then reconcile before cutover.
