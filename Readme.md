# Salamandra

In-house warehouse/event inventory manager.

Private staging operators should use [the staging runbook](docs/STAGING_RUNBOOK.md)
and [staging checklist](docs/STAGING_CHECKLIST.md). The safe environment-variable
inventory is in `config/staging.env.example`.

## Run Local GUI

Build the React/TypeScript client:

```powershell
pnpm install
pnpm build
```

Create the Python environment and install the pinned backend dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Start the Python operations server. Demo accounts and the one-click demo sign-in are
disabled unless local demo mode is explicitly enabled:

```powershell
$env:SALAMANDRA_ENABLE_DEMO="1"
.\.venv\Scripts\python.exe src/server.py
```

Then open:

```text
http://127.0.0.1:8000
```

In explicit demo mode, workspace inventory is saved to `docs/inventories.json` and
organization-owned item classes are saved to `docs/item_classes.json`.

Demo users have generated, non-documented passwords and cannot use password sign-in.
Use the **Open demo workspace** action only while explicit demo mode is enabled.

New workspace data is saved to `docs/inventories.json`, with shared inventory and per-account personal inventory.

The JSON compatibility runtime must not be exposed as a production multi-user service. Outside
demo mode, it requires the explicit `SALAMANDRA_ALLOW_JSON_DEV=1` switch.

## PostgreSQL Transactional Core

Set an isolated PostgreSQL URL, apply the schema, and rehearse the JSON migration:

```powershell
$env:SALAMANDRA_DATABASE_URL="postgresql+psycopg://salamandra:password@127.0.0.1/salamandra"
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\python.exe src/migrate_json_to_postgres.py --database-url $env:SALAMANDRA_DATABASE_URL --docs-dir docs --dry-run
```

Remove `--dry-run` only after reviewing the migration report and backing up the JSON
files. The PostgreSQL model records available, reserved, packed, and dispatched stock
separately and enforces tenant ownership, non-negative quantities, legal event states,
idempotent movement keys, and same-organization foreign keys.

Start the production persistence path with the same database URL:

```powershell
$env:SALAMANDRA_DATABASE_URL="postgresql+psycopg://salamandra:password@127.0.0.1/salamandra"
.\.venv\Scripts\python.exe src/server.py
```

This runtime uses database-backed accounts, sessions, organizations, inventory, events,
allocations, movements, item classes, integrations, and preferences. It does not write normal
application mutations to the JSON stores.

## Event Operations

Use the planner in the local GUI to choose an event item, event template, or kit. Salamandra expands requirements, checks shared and personal stock, shows dated conflicts against saved events, and only allocates items that exist in inventory.

The GUI also supports description-based planning. Describe the event in plain language and Salamandra extracts the schedule, run of show, venue, size, performers, instruments, and exclusions. It resolves capability needs to weighted real-stock candidates, adds per-item cables and power, recommends spares, rebalances overlapping events by priority, prepares checklists and a Google Calendar-compatible payload, and saves approved plans as local learning data.

Inventory operators can create and edit equipment, set model quality and handling details, and manage organization-owned item classes and dependency rules from the Inventory screen.

## Application Structure

- `frontend/` contains the React and TypeScript application source.
- `src/` contains reusable warehouse, account, event, and planning logic.
- `src/server.py` selects the PostgreSQL production runtime or explicit local compatibility mode
  and serves the production frontend.
- `web/` contains the generated production bundle.
- `tests/` contains Python unit tests for inventory, planning, accounts, organizations, and event operations.

For frontend development, keep the Python server on port `8000` and run `pnpm dev`. Vite opens the frontend on `http://127.0.0.1:5173` and proxies API requests to the Python server.
