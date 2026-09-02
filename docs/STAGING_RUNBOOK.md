# Salamandra Private Staging Runbook

Target: `https://salamandra-staging.hmimesh.com`

This runbook prepares a restricted, named-tester deployment using fake data only.
It does not authorize production inventory, public registration, DNS changes, or
deployment without an approved hosting provider and credentials.

## Deployment Summary

1. Infrastructure: a supervised Python 3.12 application process, an HTTPS reverse
   proxy or managed ingress, PostgreSQL 16, secret storage, logs, and backups.
2. Environment: load the variables in `config/staging.env.example`; replace only
   the PostgreSQL URL and verified provider proxy addresses.
3. DNS: create the provider-required `A`, `AAAA`, or `CNAME` record for
   `salamandra-staging.hmimesh.com` only after the provider is selected.
4. TLS: issue a valid certificate for the exact staging hostname and redirect
   plain HTTP to HTTPS at the edge.
5. PostgreSQL: use a staging-only database and least-privilege role with encrypted
   transport, durable storage, and backups.
6. Migration: run `.venv/bin/alembic upgrade head` as a one-shot deployment step.
7. Startup: run `.venv/bin/python src/server.py` under the process supervisor,
   listening privately on `127.0.0.1:8000` behind the edge.
8. Health: require `GET /health` and `GET /ready` to return `200` before traffic.
9. Accounts: provision Org A and Org B owners from a controlled terminal, then use
   Org A owner team management to add the lower-role tester.
10. Smoke test: execute every required section in `docs/STAGING_CHECKLIST.md` using
    test accounts, fake inventory, and three separate browser profiles.

## 1. Provider And Infrastructure Gate

Select a hosting provider before deployment. The minimum topology is:

```text
internet
  -> DNS for salamandra-staging.hmimesh.com
  -> HTTPS reverse proxy / managed ingress
  -> private 127.0.0.1:8000 Python API and built frontend
  -> private PostgreSQL 16 database
```

The edge and application may share one VM/process namespace for the first private
staging deployment. If a container platform requires `0.0.0.0:$PORT`, record that
provider requirement and add a reviewed startup adapter before deploying; the
current server intentionally defaults to a private loopback listener.

Required provider capabilities:

- persistent PostgreSQL storage and encrypted connections
- secret/environment injection without committing a completed environment file
- process supervision, graceful restart, and immutable release artifacts
- HTTPS certificate management and HTTP-to-HTTPS redirect
- request-size and sign-in rate limits at the edge
- retained application/proxy logs with secret redaction
- PostgreSQL backup and restore support

## 2. Exact Staging Environment

Use `config/staging.env.example` as the variable inventory. Safe fixed values are:

```dotenv
SALAMANDRA_ENV=staging
SALAMANDRA_APP_ORIGIN=https://salamandra-staging.hmimesh.com
SALAMANDRA_ALLOWED_ORIGINS=https://salamandra-staging.hmimesh.com
SALAMANDRA_TRUSTED_HOSTS=salamandra-staging.hmimesh.com
SALAMANDRA_COOKIE_SECURE=true
SALAMANDRA_SESSION_MAX_AGE_SECONDS=43200
SALAMANDRA_LOG_LEVEL=INFO
SALAMANDRA_TRUST_PROXY_HEADERS=false
SALAMANDRA_TRUSTED_PROXY_IPS=
SALAMANDRA_ENABLE_DEMO=false
SALAMANDRA_ALLOW_JSON_DEV=false
```

Store this completed value only in the provider secret manager:

```dotenv
SALAMANDRA_DATABASE_URL=postgresql+psycopg://<staging-user>:<secret>@<staging-db-host>:5432/<staging-db-name>?sslmode=require
```

Keep proxy-header trust disabled until the provider supplies the exact direct
proxy IP addresses. If enabled, set `SALAMANDRA_TRUST_PROXY_HEADERS=true` and list
only those IP addresses in `SALAMANDRA_TRUSTED_PROXY_IPS`. Never enter public
client ranges or trust forwarded headers from arbitrary sources.

Staging startup fails closed if PostgreSQL, HTTPS origin, allowed origins, trusted
host, secure cookies, or the current Alembic revision is missing. Demo and JSON
fallback cannot be enabled in staging.

## 3. Build The Release Artifact

Build from the reviewed staging branch or immutable commit:

```bash
python3.12 -m venv .venv
.venv/bin/pip install --requirement requirements.txt
corepack enable
pnpm install --frozen-lockfile
pnpm run check
pnpm run build
.venv/bin/python -m unittest discover -s tests
```

The deployed artifact must include `src/`, `migrations/`, `alembic.ini`, and the
built `web/` directory. Node.js is not required by the running Python process when
`web/` was built before release.

## 4. Provision PostgreSQL

Create a staging-only PostgreSQL database and least-privilege application role.
Use a separate migration role when the provider supports it. Require TLS, enable
durable storage and encrypted backups, and record the restore operator outside the
repository. Do not import JSON or real warehouse data for this phase.

Verify connectivity from the release environment without printing the URL:

```bash
.venv/bin/python -c "from sqlalchemy import create_engine,text; import os; e=create_engine(os.environ['SALAMANDRA_DATABASE_URL']); print(e.connect().execute(text('select 1')).scalar())"
```

## 5. Run Migrations

Load the staging environment through the provider secret mechanism, then run:

```bash
.venv/bin/alembic upgrade head
.venv/bin/alembic current
.venv/bin/alembic check
```

Required order:

```text
provision PostgreSQL
  -> configure secrets/environment
  -> run Alembic migrations
  -> start application
  -> verify /health and /ready
  -> provision staging users
  -> execute smoke tests
```

Migrations are a one-shot release task. The application does not and must not run
schema migrations during arbitrary requests. Startup and `/ready` reject a schema
whose Alembic revision is not the repository head.

## 6. Start The Application And HTTPS Edge

Start the service under the provider supervisor:

```bash
exec .venv/bin/python src/server.py
```

The reference topology forwards the exact public host to
`http://127.0.0.1:8000`. Do not expose that listener directly to the internet. A
minimal Caddy shape is shown only as a provider-neutral reference:

```caddyfile
salamandra-staging.hmimesh.com {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8000
}
```

The final edge configuration must also redirect HTTP, preserve the original Host,
limit request bodies, apply an additional sign-in rate limit, and avoid logging
cookies or authorization data. Do not apply this configuration or request a
certificate until DNS and provider authorization exist.

## 7. Verify Health And Readiness

Before public routing, verify the private listener through the accepted Host:

```bash
curl --fail-with-body -H 'Host: salamandra-staging.hmimesh.com' http://127.0.0.1:8000/health
curl --fail-with-body -H 'Host: salamandra-staging.hmimesh.com' http://127.0.0.1:8000/ready
```

After TLS and DNS are authorized and configured:

```bash
curl --fail-with-body https://salamandra-staging.hmimesh.com/health
curl --fail-with-body https://salamandra-staging.hmimesh.com/ready
python scripts/staging_public_smoke.py
```

`/health` proves process liveness only. `/ready` returns `200` only when PostgreSQL
is reachable and the migration revision is current. Neither endpoint exposes
database details, credentials, tenant data, or revision identifiers.

The smoke script is non-destructive. It also verifies TLS, the HTTP redirect,
security headers, signed-out state privacy, and rejection of a forged Host header.

## 8. Provision Controlled Test Accounts

Create Org A owner after migrations from a controlled terminal:

```bash
.venv/bin/python src/provision_admin.py \
  --organization-id staging-org-a \
  --organization-name 'Staging Organization A' \
  --email <org-a-owner-test-email> \
  --name 'Org A Owner'
```

Create Org B owner with the same command and a separate organization:

```bash
.venv/bin/python src/provision_admin.py \
  --organization-id staging-org-b \
  --organization-name 'Staging Organization B' \
  --email <org-b-owner-test-email> \
  --name 'Org B Owner'
```

The command reads a temporary password without echo and does not accept a password
argument. Sign in as Org A owner and use Team management to create a named
technician (or operator) in Org A. Minimum test identities are:

- Org A owner: event, organization, inventory, and team administration
- Org A technician/operator: lower-role authorization testing
- Org B owner: tenant-isolation testing

Use fake email addresses controlled by the staging team and unique temporary
passwords shared out of band. Do not reuse customer or employee passwords. Public
registration and demo sign-in remain unavailable.

## 9. Execute The Staging Smoke Test

Use three isolated browser profiles or private browser contexts, one per minimum
test identity. Complete `docs/STAGING_CHECKLIST.md` and retain its evidence record.
Use fake inventory and events only.

The minimum workflow is:

```text
create event -> confirm/allocation -> packing checklist -> packed
-> dispatch -> return checklist -> returned
```

Also execute kit checkout, CSV import/export, duplicate idempotency retries, Org A
to Org B probes, lower-role direct API calls, and representative simultaneous
operations. Every stock change must be explainable by the PostgreSQL movement or
adjustment ledger.

## 10. Restart Test

1. Sign in and create fake inventory plus an event in Org A.
2. Record the event ID, holding quantities, session cookie presence, and `/ready`.
3. Remove the application instance from traffic and stop only the app process.
4. Keep PostgreSQL running and restart the same immutable release artifact.
5. Wait for private and public `/ready` to return `200`.
6. Reload using the existing browser cookie; the durable active session should
   remain authenticated.
7. Verify the event, inventory, allocation, checklists, movement history, and user
   preferences persisted.
8. Confirm all JSON compatibility file hashes are unchanged.

Repeat with logged-out, expired, revoked, disabled-user, and disabled-membership
sessions; those cookies must remain unauthenticated after restart.

## 11. Deployment Safety And Evidence

- Never commit database URLs, passwords, session cookies, tokens, or integration
  secrets.
- Keep `SALAMANDRA_LOG_LEVEL=INFO`; do not expose debug tracebacks in responses.
- Use fake staging inventory, events, organizations, and integrations only.
- Record the release commit, provider, database, Alembic head, tester, timestamps,
  screenshots, redacted request IDs, and PASS/FAIL results.
- Freeze dispatch/return testing if inventory conservation or ledger reconciliation
  fails.

## 12. Rollback

Keep the previous immutable application artifact. If deployment fails:

1. Remove the new instance from traffic.
2. Preserve logs and correlation IDs.
3. Re-route to the previous artifact only when its expected schema is compatible.
4. Verify `/ready`, sign-in, inventory reads, and an event read before reopening.
5. If integrity may be affected, freeze inventory movements and reconcile the
   ledger before further dispatch or return.

Do not blindly downgrade a populated staging database. Rehearse downgrade only on
a disposable database. Use a tested backup restore into a separate database for
recovery when schema compatibility is uncertain.
