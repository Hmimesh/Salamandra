# Operations Reality Foundation: Phase F

Status: implemented, integrated-debugged and frozen for the authorized Phase F
commit/push. No manual deployment or Senior review is part of this freeze.
Branch: staging-deployment. Baseline: f0d80aec3f89c7db1af36960ef88b3e47e3cc4c9.

## Implemented Editing Slice

Generated drafts expose Edit requirements. Operators can change quantities,
capabilities and priorities, add or remove requirements, then recalculate before
saving. The original brief and crew survive this handoff to structured planning.

Saved planning events expose Edit plan. Requirement changes use the existing
authenticated /api/events/update command, with event ID and expected version.
The server rebuilds the plan and checklists; the browser does not submit trusted
allocation or movement state. Unedited requirement connector, quality and
attribute constraints are retained. Metadata-only edits keep the existing
planning-mode behavior. Conflicts leave the edit form open for recovery.

Existing Phase C capture_edit_in_session compares a saved event's immutable
proposal with the updated authoritative requirements. This preserves the
original saved proposal across repeated updates.

The first generated UI preview now requests a server-owned `event_proposals`
snapshot. Recalculation retains its opaque reference. Event creation consumes
the reference in its existing transaction and copies original evidence into
Phase C alongside the final corrected requirements. Foreign/absent references
are nondisclosing; another actor cannot consume the reference. A second event
cannot reuse it, while replaying the original event-creation command still works.
Failure rolls back event creation and proposal consumption together. Older API
clients that omit the reference retain their original creation behavior.
Migration 0012 adds only the proposal table and its ownership index/constraint.
Snapshots contain the original planning request and compact proposal, not trusted
client allocation state. Preview capture uses an actor-bound operation receipt:
same-key retry returns the original displayed event and snapshot reference,
even if a later planner computation differs; conflicting input returns 409.
The browser retains the key for retries of an unchanged build request. No automatic
purge or expiry is applied to original evidence in this phase. A future retention
policy must preserve evidence linked to events and audit history.

Confirmed, packed and out events remain locked against destructive plan editing.
Packed/out events now use the separate field-adjustment commands below.

## Warehouse Condition Slice

Migration 0013 adds a nonnegative condition-held bucket to existing holdings,
quantity-based condition incidents, and append-only condition movements.
Owned total includes ready, reserved, packed, dispatched and condition-held units.
Allocation continues to consume ready stock only. Retirement keeps ownership
history; it is not a stock sale or deletion.

POST /api/maintenance/report accepts source scope, item ID, quantity, initial
condition, issue and idempotency key. It can move only ready warehouse stock into
needs_repair, quarantine, missing or retired. It cannot reconcile dispatched
equipment. POST /api/maintenance/transition requires incident ID, version, target
condition, reason and idempotency key. Incident batches transition together:
needs_repair -> in_repair -> ready; quarantine -> ready/needs_repair; missing ->
ready. Retirement is terminal. An additional repair failure can return in_repair
to needs_repair. Each command updates the holding and incident, appends a condition
movement and audit record, and completes its receipt in one transaction.

Condition movement records identify source/destination states, exact quantity,
actor, incident, before/after ready balances, reason and time. Duplicate commands
replay without restoring or consuming units twice. Same-key conflicting requests
fail. Holdings lock before incidents; membership/permissions are rechecked on
every request. Personal holdings remain visible only to their owner, including
when the actor otherwise has administrative permissions.

Central permissions: owner/admin/operator can read and manage Maintenance;
technicians can report issues through the authenticated API but cannot restore
stock or retire it. GET /api/maintenance returns at most 100 incidents in stable
reported-date/ID order and supports a condition filter. The manager UI uses
human-readable labels and the existing searchable inventory picker. Clear and
archive refuse definitions with condition-held units. Protected definition edits
also remain blocked while condition-held units exist.

Maintenance shows quantity totals for needs repair, in repair, quarantine,
missing and retired. A scoped database aggregate counts units, not incident rows,
independently of the selected filter and 100-row display limit. Resolved incidents
do not contribute to non-ready totals. Other organizations and other users'
personal holdings never contribute to these counts.

Partial incident-batch resolution, assigned repair personnel and incident pagination
remain outside this delivered foundation. The expanded Inventory summary is now
implemented. The condition ledger supplements rather than rewrites event
dispatch/return movements.

## Inspected Event Returns

The Returns page exposes Inspect return for dispatched events. The dialog loads
the authoritative dispatched holdings and current event version, then collects
ready, damaged and missing quantities. Each holding must reconcile exactly;
damaged or missing units require an issue description. Damaged units enter
needs_repair. Missing units enter missing. Both retain ownership and remain
unavailable for allocation.

GET /api/events/return?event_id=... is scoped to the authenticated organization
and requires operations.return. POST /api/events/return accepts event_id,
version, idempotency_key and lines (holding_id, ready, damaged, missing, reason).
It rejects unexpected fields and unrecognized/duplicate holdings. There is no
client-provided organization, actor or operational status.

One transaction acquires the operation receipt, locks the event and exact source
holdings in deterministic order, validates the version and quantities, completes
the existing return transition, and applies condition dispositions before commit.
The temporary gross return balance is never committed independently or visible
to another allocator. The returned movement retains the gross reconciled quantity
and records final ready/damaged/missing dispositions. Condition movements record
the transfer of non-ready units from that gross return into condition stock;
they are not additional physical returns. Initial dispatch movements are immutable.

Condition incidents preserve an organization-scoped event foreign key. Event
learning execution captures the final returned-movement disposition breakdown,
without changing similarity/retrieval rules. A failure at any point rolls back
checklists, event status, stock, incidents, movements, learning updates and the
receipt. An identical command retry returns the original result; a new command
against an already returned event conflicts. Existing all-ready status commands
remain compatible and serialize against inspection on the same event lock.

Verification checkpoint: 284 Python tests, 78 PostgreSQL-required tests and 26
race-required tests passed with zero skips. The new inspected-return browser test
passed on all five configured viewport projects. TypeScript/Vite and the
PostgreSQL upgrade/check/downgrade/re-upgrade/check sequence passed. The complete
browser matrix still needs rerunning for the eventual Phase F completion gate.

## Integrated Operational Work

Field adjustments, logistics, crew and external assignment access are connected
to the existing event lifecycle. The complete Python/PostgreSQL and browser gates
passed. No Phase C/D/E redesign or
inventory/dispatch/return replacement was introduced.

## Completion Gate

Every new command must enforce centralized permissions, organization ownership,
version checks where applicable, idempotency, auditability and atomic database
transactions. New physical changes must be explainable by the stock ledger.
Use explicit Alembic migrations and disposable PostgreSQL verification.

The requested HTTP isolation, two-process race, lifecycle, Unicode, migration and
complete responsive/browser suites passed with zero required skips. The freeze
authorizes committing and pushing only to origin/staging-deployment after file
inspection. Railway may deploy automatically from that push. Do not merge, deploy
manually, start a Senior review, or modify staging data, Railway variables or DNS.

No learning evaluation or Senior review is part of this phase. ML, autonomous
actions, rentals/procurement, financial workflows, notifications, rider parsing,
localization infrastructure and reporting remain deferred.

## Field Adjustment Implementation

Migration 0015 adds organization-scoped field_adjustments and a released state
for retained allocation lines. Existing allocation rows remain the mutable
physical projection; historical stock movements remain immutable.

GET /api/events/adjustments is an event-scoped bounded history and physical-line
read. POST to that path requests additions by capability or reductions against
exact event holdings, with event version, reason and idempotency key. Additions
and reductions are separate requests so each has an unambiguous physical
completion. Creating a request changes no stock. Pending reductions cannot
collectively exceed currently allocated quantities.

POST /api/events/adjustments/preview uses the existing planner, configured item
classes and the event owner's eligible personal/shared ready stock. The response
includes dependencies, substitutes, missing quantities, version and a fingerprint
of the reviewed plan. Transport estimates describe the additional load.

POST /api/events/adjustments/fulfill rechecks event/adjustment versions and the
preview fingerprint after locking the selected source holdings. All required
stock must be ready. The transaction mutates current allocation lines and stock,
appends a release/supplemental_pack/supplemental_dispatch movement, refreshes
physical packing/return checklists, captures learning execution and completes an
idempotency receipt. A failure leaves the request pending and rolls everything
back. No original proposal or earlier dispatch movement is rewritten.

POST /api/events/adjustments/cancel cancels only a pending request; it never moves
stock. Packed reductions require explicit physical release. Dispatched
reductions remain out until inspected return. Terminal return resolves those
reductions and cancels unfulfilled additions with a closure audit; event
cancellation cancels pending adjustments as part of its existing transaction.

The Changes section supports request, review, fulfillment and cancellation.
events.adjust applies to owner/admin/operator/producer/technician; fulfillment
also requires the existing pack or dispatch permission for the current state.
Tests cover immutable dispatch, mixed inspected return after supplemental
dispatch, retained releases, rollback, scope, stale versions, conflicting keys,
and two-process duplicate fulfillment/fulfillment-versus-return races.

## Logistics Implementation

Migration 0016 adds indexed logistics-window columns to events. Optional
milestones and operational notes are stored separately from show date/time and
duration. The existing Asia/Jerusalem default is retained. Local times are
normalized to UTC; nonexistent/ambiguous daylight-saving times require an
explicit offset instead of being silently shifted.

GET/POST /api/events/logistics reads/updates the schedule. The reservation window
starts at the earliest preparation/packing/standby/dispatch/load-in/setup time
and ends at the latest show-end/teardown/return time. Planning conflict checks
use these windows; already reserved/packed/dispatched stock remains held by the
existing allocator regardless of future calendar changes. Planning-event edits
preserve logistics and revalidate its window against any changed show schedule.

POST /api/events/logistics/stage marks/unmarks standby only for packed events.
No inventory bucket changes. Version checks, receipts and audits apply to both
commands. Completed preparation/packing/staging/dispatch timestamps cannot be
rewritten. Closed events reject changes. Standby is displayed separately from
show duration, not introduced as a second operational event lifecycle.

The event Logistics section provides editable milestones, notes, standby control
and GET /api/events/logistics/export CSV. CSV cells use the existing spreadsheet
formula protection. Export is organization scoped and contains operational
information only, including assigned crew names, roles and call/release times,
without profile contact details or internal crew notes. logistics.edit applies to owner/admin/operator and
producer; staging uses existing operations.pack authorization.

## Crew / External Implementation

Profiles and external links are managed by owner/admin/
operator. Producers can additionally assign crew and edit logistics. Crew
overlaps are blocked unless owner/admin/operator provides an audited override;
skill mismatches require acknowledgement under the same override authority.
External equipment is explicitly selected per assignment, empty by default.
External links expire at assignment release plus 24 hours, fixed on issuance;
later schedule edits never silently extend access. No early partial-return
workflow is part of this phase. No notifications, HR or financial features.

Migration 0017 adds crew_profiles, crew_skills, crew_roles, crew_assignments and
crew_access. Same-organization composite foreign keys protect role/event,
assignment/profile and credential/assignment relationships. Skills are normalized
rows with deterministic levels 1 (basic), 2 (intermediate), 3 (advanced). Skill
keys use Unicode NFKC/casefold; names and operational free text retain their exact
UTF-8 content. Profiles are operational personnel records, not login accounts;
creating an internal profile does not grant membership. Existing assigned_user_ids
continue unchanged, without fabricated historical shifts or qualifications.

GET/POST /api/crew lists bounded profiles (500) and creates/version-updates them.
GET /api/events/crew reads scoped roles, assignments and physical equipment.
POST /api/events/crew/role defines role quantity, skills and complexity.
POST /api/events/crew/assign creates or version-updates call/release windows,
assignment notes, status and explicit equipment selection. Windows use half-open
overlap semantics: one release exactly at another call is not a conflict.
Assignments cannot exceed their role's requested headcount. Active role requirements
cannot be edited underneath an assignment; cancel assignments before editing a role.
Cancelled assignments cannot be reopened, and closed events refuse crew edits.
Drafts with crew records must be cancelled, not hard-deleted.

Every command derives organization/actor from authentication, checks centralized
crew.read/manage/assign/override/access permissions, and acquires its organization-
scoped receipt before mutation. Event and crew-profile locks serialize assignment
changes; profile locks cover overlap and skill validation. Event locks serialize
headcount checks. A conflicting reuse of a request key returns 409. Versions,
assignment/profile data, audit record and receipt commit together. No command in
this service mutates stock, allocations, dispatch, returns or condition records.
Profile skills can be operator-maintained later without rewriting past audit records.

POST /api/events/crew/access issues/regenerates or revokes an assignment link.
Only active external profiles on active assignments may receive a link. Issuance
revokes all previous credentials for that assignment. Secrets contain 256 bits
of randomness; only SHA-256 hashes are persisted. The raw secret is returned once,
never stored in receipts/audits. Identical issue retries return the original
metadata with token=null; a lost response requires explicit regeneration. Expiry
is fixed at release+24h at issuance and cannot be extended by editing the schedule.
Shortening release within the assignment transaction revokes all assignment
credentials if it reduces any unrevoked credential's access window. The assignment
audit records `access_revocation_reason=release_shortened`. A later extension cannot
revive those credentials; explicit reissue uses the current release time. Every
external read independently enforces the earlier of stored expiry and the current
assignment release+24h, even if a stale credential was not revoked.
Deactivating a profile, changing it to internal or cancelling an assignment revokes
its links. Reactivation does not revive old credentials. Event cancellation is
also checked on every external read. Returned events remain readable until expiry.

/external#<secret> sends its fragment credential only in an Authorization bearer
header to GET /api/external/assignment, without session cookies.
The page captures the fragment in component memory and removes it with history
replacement before fetching, preserving the pathname, query and router state.
No token is persisted in storage or cookies. In-page Refresh retains access; full
reload without a fragment requires reopening the original link and sends no empty
Bearer request. Reopening the original fragment in the same tab also recaptures
and immediately removes it. Capture is safe under React StrictMode.
It does not grant
workspace authentication. Invalid, expired, revoked and cancelled access returns
the same not-found response. No organization/event/profile/holding IDs, other
personnel, contacts, internal notes, inventory browser, maintenance incidents,
learning records or raw audit history appear in this projection.

The view returns event/venue, own name/role/call/release/notes, logistics milestones
and explicitly shared physical equipment. General logistics notes are not copied
to external personnel. Current quantities come from authoritative allocation lines;
unselected new equipment never appears automatically. A bounded 20-entry feed
contains own assignment updates, sanitized logistics changes and changes involving
selected equipment. Existing event JSON is read, not duplicated into a second
history architecture. Read-only REPEATABLE READ provides one committed snapshot
without write locks. The visible page polls every 30 seconds and supports Refresh;
hidden pages do not poll. Link revocation clears previously displayed data on the
next read. No notification provider, cache or service is introduced.

Team exposes profile creation/editing and skill rows. Event detail exposes role
creation, assignment/edit/cancel, override reason and link issue/revoke controls.
All controls use existing application styling and modal primitives. The separate
external page has no workspace navigation or write controls.

## Review Remediation Boundaries

Logistics CSV requires both `state.read` and `crew.read` before event lookup or
construction. Existing tenant scope, UTF-8 and spreadsheet formula escaping remain
unchanged. Clients and read-only users cannot obtain personnel through this export.

Server proposal capture and consumption use one versioned SHA-256 intake binding
over canonical JSON containing only the original description after `.strip()`.
Internal whitespace, punctuation, case and Unicode remain exact. When both request
and generated descriptions exist they must agree. Existing proposals derive the
binding from stored original request/brief; no browser fingerprint or new column
is accepted. Title, time, location, attendance, duration, crew, planning mode and
requirements remain editable. A mismatched brief returns 409 under the existing
proposal lock before copying evidence or consuming the proposal; event creation
and its receipt roll back. A legitimate existing creation replay is unchanged.

Events and Returns both use `ReturnInspection`, including all-ready equipment.
Events hides its detail dialog during inspection; cancellation restores detail and
focus to Inspect return. Successful reconciliation uses the existing workspace
mutation flow. Normal Events UI never posts `status=returned`; the all-ready
backend transition remains solely for existing API compatibility. This remediation
does not change authoritative return reconciliation, ledger history or migrations.

## Inventory Operational Summary

GET /api/inventory/operations requires inventory.read and derives the organization
and actor from the current session. Shared, own-personal and combined scopes use
the existing holding visibility rule. A committed read snapshot aggregates Ready,
Reserved, Packed, Standby, Out, Needs repair, In repair, Quarantine, Missing and
Retired quantities. Standby is a subset of packed holdings and is subtracted from
the displayed Packed total, so the summary does not double count units. No other
user's personal holding or another organization's condition incident contributes.

## Verification Evidence

The final integrated-debug gate passed with 327 Python tests, including 102 PostgreSQL-required
tests and 30 race-marked tests, zero skips and zero failures. This includes the
additional PostgreSQL snapshot-race regression.

The integrated HTTP lifecycle additionally verifies packed releases, supplemental
dispatch, crew/logistics, external expiry/revocation, split return, immutable
movement/audit records and bidirectional tenant probes in fresh PostgreSQL 16.15
schemas. HTTP fixtures apply real Alembic migrations rather than metadata creation.

New focused suites: tests/test_field_adjustments.py, tests/test_event_logistics.py,
tests/test_event_crew.py. Existing PostgreSQL HTTP tests now exercise two API
processes for supplemental fulfillment, fulfillment-versus-inspected-return,
logistics edits, crew overlap, duplicate token issuance and assignment edits.
The external snapshot test pauses a read while personnel and assignment updates
commit, proving a response cannot mix committed versions.

The crew browser flow passed all five viewport projects after fixing a genuine
mobile overflow caused by an unconstrained brand image. An earlier selector used
the wrapped select's label rather than its combobox role; the test now selects by
accessible role/name. No assertions or tests were removed. The full matrix passed
130/130 checks with zero skips/failures at 360, 768, 1280, 1440 and the configured
200%-equivalent viewport. See PHASE_F_VERIFICATION.md for the full handoff.

Explicit PostgreSQL upgrade/check/downgrade-to-base/re-upgrade/check through 0017
passed in a disposable schema. TypeScript and the Vite production build passed.
No staging database, Railway configuration, DNS, branch or Git remote was changed.
