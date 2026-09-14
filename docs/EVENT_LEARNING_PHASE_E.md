# Event Learning Phase E

Implementation contract. Verification results are reported separately after the
complete suites run. No Phase F work or Senior review is included.

## Purpose

Measure whether deterministic historical suggestions are useful, without model
training, external providers, cross-customer learning, or automatic plan edits.
The accepted Phase C evidence and Phase D scoring remain authoritative.

## Candidate Retrieval (Implemented)

Planning calls `EventLearningStore.examples(organization_id, limit, features=...)`.
The existing no-features Phase C contract retains its ID ordering. Both paths
clamp the candidate count to 1..100. Features use the Phase D validator.

The planning path selects up to four bounded pools, in priority order:

1. All supplied department/venue conditions, plus attendance and duration within
   half to twice the requested value where those numeric features are present.
2. Any overlapping department, when departments are supplied.
3. Exact structured venue, when supplied.
4. Recent eligible history as a fallback.

Each pool limits its rows to 100 before union. Candidate IDs are deduplicated,
ordered by strongest pool, creation timestamp descending, then stable event ID,
and limited to 100 again before joining feedback and affected-item evidence.
Empty features use only the fallback pool. Similarity still refuses to recommend
from insufficient dimensions. Raw briefs and operator notes are not searched.

Every pool requires authenticated organization scope, eligible real-source
history, no exclusion reason, and a returned event. All selection, eligibility,
feedback, and affected items are read in one SQL statement. A concurrent edit
can therefore expose the old or new committed version, not a mixture assembled
by separate queries. Reads do not add write locks.

PostgreSQL uses a GIN `jsonb_path_ops` index over structured features for
department/venue containment and a composite organization/eligibility/creation
index for fallback retrieval. PostgreSQL still evaluates and sorts qualifying
rows internally; bounded returned rows are not a claim of constant database
work. No unbounded history is materialized in Python.

Recency selects candidate pools only. It does not change similarity scores or
their final tie breaker. An older exact match outranks a recent irrelevant
candidate after scoring. With more than 100 equally prefiltered matches, older
examples can still be outside the candidate budget; this is deliberate.

Bounds: one candidate SQL statement, at most four 100-row pools, at most 100
distinct examples, eight ranked matches, and twelve suggestions. Feedback item
joins retain the existing Phase C limits. No N+1 queries are introduced.

## Scoring (Unchanged)

Departments: 35; attendance: 30; duration: 15; venue: 20. Scores remain 0..100,
with the existing minimum 60 and at least two matching dimensions. Missing
features contribute zero. Existing correction/feedback exclusions and quantity
aggregation are unchanged. Quantity scaling is not enabled or experimented on.

## Feature Provenance And Audit

The event model currently persists attendee count, duration, venue kind, event
size, requirements, and milestones. Several Phase C placeholders such as event
type and indoor/outdoor are not independently supplied by the event model.
Do not treat these placeholders as reliable new scoring dimensions.

`EventRecord.feature_sources` is a small JSON map, round-tripped with the event
and captured in Phase C normalized features as `provenance`:

- `operator`: a numeric override supplied in the planning request.
- `structured`: departments from manual requirements, or duration calculated
  from existing parsed schedule milestones.
- `intake`: a value obtained from the existing description parser.
- `defaulted`: the existing 240-minute duration fallback.
- `unknown`: missing information, or old records without provenance.

Attendance, duration, venue kind and departments are covered. No new keywords
or raw-note interpretation were added. A reliably derived `multi_day` flag
(recorded duration >= 1440 minutes) is retained for diagnostics, not scored.
Unpopulated category/performer/indoor-outdoor placeholders remain unscored.

Operator means supplied in the request, not proof of a deliberate keystroke:
existing forms can submit prefilled values. Historical provenance is not
retroactively guessed. Draft-provided provenance is descriptive, never an
authorization or operational-state input. The immutable suggestion snapshot
includes the structured features and provenance used for its explanation.
Default duration produces an explicit warning in that snapshot and the UI.
Phase D still compares recorded operational durations, including defaults, with
its existing weight. Provenance does not introduce new weights or change the
completed candidate prefilter. Missing values still contribute zero.

## Schema And Immutable Evidence

Migration `0011_suggestion_outcomes` follows the candidate-index migration.

`suggestion_sessions` stores server-generated identity, organization, actor,
optional event FK, retained event ID/title snapshots, event version, timestamps,
semantic versions, evidence count, compact immutable suggestion JSON, one
interaction action, applied quantities, and quantities committed at event save.
Composite foreign keys enforce same-organization event and membership ownership.
Draft sessions have no event FK until the normal event-save transaction links
them. They are explicitly shown as unsaved drafts, not invented event records.

The scoring version is **D1**; retrieval is **E1**. They are separate because
Phase E changes retrieval, not scoring. Git revisions are not semantic versions.
Snapshots retain the bounded suggestions, observed ranges, evidence counts,
generic reasons/warnings and capability codes. Inventory renames do not change
these codes. Event titles are snapshotted when associated, including UTF-8.

`suggestion_evaluations` stores immutable comparison arrays and the exact Phase C
learning/feedback versions used. Uniqueness is session + learning version +
feedback version. Same-organization session FKs prevent foreign linkage.

## Session And Interaction Lifecycle

`POST /api/events/learning/sessions` accepts validated structured features,
optional provenance and saved-event/version context, and an idempotency key.
It generates the result server-side; the browser cannot supply the snapshot.
The browser requests once per completed preview/review, not on every keystroke
or rerender. No passive `viewed` action is recorded. Empty evidence creates no
suggestion session, though the command receipt remains replayable.

`POST /api/events/learning/outcome` accepts session ID, Ignore or Apply, applied
quantities, and an idempotency key. Actor ownership is checked independently of
organization and role. Apply must cover exactly the shown capabilities, with
integer quantities 1..10000. Equal quantities produce `applied`; different
quantities produce `applied_with_edits`; Ignore requires an empty quantity map.

The authoritative command records acceptance into the draft, not a claim of
stock movement or saved-event success. Apply updates the local manual plan only
after that command succeeds. The command is the interaction, not a separate
nonessential analytics request. Failures remain visible and retryable with the
same key; local plan data is retained. Ignore hides immediately, shows pending
status, and restores the panel with an error if saving fails. Review after an
ignored response generates a fresh session instead of rewriting the old action.
Double clicks and stale asynchronous responses cannot apply an old panel to a
new draft. Recalculation and the existing event-save command remain required.

`/api/events/save` can carry `suggestion_session_id`. It verifies organization,
actor, prior Apply and unused linkage, then associates the record and captures
the actual committed requirement quantities inside the existing creation
transaction. A failed link rolls back creation. No hidden allocation, dispatch,
return or inventory mutation is introduced by suggestion commands.

## Return Comparisons

Return and feedback-save transactions call the evaluator while holding their
existing event lock. Owners/admins can also explicitly retry evaluation with
`POST /api/events/learning/evaluate` and an organization-scoped event ID.

For each suggested capability retain **shown, draft-applied, initially committed,
and final corrected requirement quantities**. Classification compares final to
the operator-applied quantity:

| Classification | Rule |
| --- | --- |
| insufficient_evidence | No Phase C return movement evidence, non-real source, or no applied quantity |
| unresolved_feedback | The review reports unresolved equipment issues, a non-about-right fit, or a plan not reusable unchanged |
| removed | Final capability quantity is zero |
| retained | Final equals applied |
| increased | Final is greater than applied |
| decreased | Final is less than applied and greater than zero |

Feedback classification is intentionally conservative across the event: an
unresolved issue prevents a confident retained classification. Private notes
are neither interpreted nor copied. Comparison is with corrected requirements,
not a claim that every dispatched unit was used on site or caused success.

Later feedback creates a new versioned evaluation. Earlier arrays are never
rewritten. Repeated evaluation of the same evidence version is a no-op. The
summary uses the latest version per session, not the sum of all revisions.
Eligibility edits do not rewrite snapshots/actions; they still control future
candidate retrieval independently. Returning twice does not double-evaluate.

## Permissions And Privacy

Central policy is in `src/security.py`:

- `events.plan`: generate suggestions (existing planning roles).
- `learning.outcome.write`: owner/admin/producer/operator; only their own sessions.
- `learning.summary.read`: owner/admin organization summaries.
- `learning.history.read`: owner/admin explicit returned evaluation access.
- `events.learning.manage`: unchanged owner/admin eligibility administration.

The Settings visibility flag comes from server permissions. It is not the API
authorization boundary. Organization comes only from authenticated DB context;
foreign and absent valid resource IDs receive the same nondisclosing 404.
Requests are bounded to 8 KiB and reject unsupported fields, invalid enums,
quantities and identifiers. No client role/organization field grants authority.

Actor identity supports ownership and auditing, not a public operator scoreboard.
No briefs, private notes, complete historical events, browsing history,
keystrokes, fingerprints or unrelated navigation are copied into telemetry.
Unconfirmed event deletion detaches its FK under the event lock, preserving
session ID/title snapshots. Disabled users cannot authenticate or act; historical
evidence remains. Kits, inventory, Phase C history and account lifecycle are not
deleted by this feature. No automatic retention purge is introduced.

## Transaction And Concurrency Model

Command receipts reuse `operation_requests` with organization + operation + key
uniqueness and a fingerprint that includes the actor. A savepoint/unique insert
arbitrates acquisition across processes. Same-key conflicting input returns 409;
identical retries replay the committed result. Different keys cannot overwrite
a session's settled action. Identical settled responses may be acknowledged
under a new key without adding another action/audit entry.

Lock order is event (when associated), suggestion session, then command receipt.
Generation locks its optional event then its command receipt. Lifecycle writes
retain their event lock and append complete evaluations in the same transaction.
No new organization/table-wide locks are introduced. Saved-event Apply checks
the version after acquiring the event lock. Summary reads use committed data
without write locks. The candidate SELECT runs inside the generation transaction
and remains one joined statement with the existing eligibility contract.

## Summary And Query Bounds

Settings -> Learning shows eligible events, sessions, and action counts with
the denominator **completed responses** (ignored + both applied categories).
Applied counts are explicitly draft acceptance. Returned classification counts
use **compared suggestions** as their denominator. There is no accuracy score.

After DB membership authorization, one summary statement computes all counts,
latest-version classification aggregates, and the latest 20 sessions. JSON
comparison expansion and aggregation run in SQL. The result materialized in
Python has at most 20 x 6 rows (recent sessions x outcome categories), rather
than the whole organization history. Recent records include no actor names or
private notes. SQL work grows with the organization's history; this is not a
constant-time or internet-scale analytics claim. Indexes cover organization,
actions, event linkage, session ownership, and version uniqueness.

The >100-history regression uses 305 records and places relevant older records
beyond the former ID-first sample. Both repository and authenticated HTTP tests
verify discovery, stable ranking, tenant isolation, and unchanged state. Full
acceptance also includes real PostgreSQL retries/races, migration round trip,
the Coffee-House regression, and every configured Playwright project.

## Phase F Boundary

No quantity scaling, embeddings, model-assisted planning, or learned weights.
Any future experiment requires separate authorization and evidence.
