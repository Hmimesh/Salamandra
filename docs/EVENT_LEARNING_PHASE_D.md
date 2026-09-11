# Event Learning Phase D

Phase D is deterministic planning assistance, not machine learning. It does not
train a model, call external providers, or mutate a plan without operator action.

## Retrieval and scoring

`EventSimilarityService` calls the Phase C contract
`EventLearningStore.examples(organization_id, 100)`. The store uses one SQL
statement, an organization/eligibility index, and a limited candidate subquery
before joining feedback items. Candidates are stably ordered by event ID; this
is a bounded sample, not an exhaustive or recency-ranked search of history.
Only same-organization, returned, real-source, eligible, non-excluded records
participate. The current event ID, when supplied, is excluded before ranking.

The following dimensions are populated by the existing Phase C feature contract.
Other fields such as event type and performer counts are too sparsely populated
to justify weights in this phase. Raw brief text and notes never affect scoring.

| Dimension | Input range | Weight | Comparison and reason |
| --- | --- | --- | --- |
| Departments | Up to 32 normalized department codes | 35 | Jaccard overlap; operational scope matters most |
| Attendance | Integer 1..1,000,000 | 30 | Smaller/larger ratio; prevents large scale mismatches |
| Duration | Integer 1..10,080 minutes | 15 | Smaller/larger ratio; similar operating periods |
| Venue profile | Existing normalized venue kind, up to 80 characters | 20 | Exact equality; venue constraints matter |

Each comparison is 0..1. Multiply by the explicit weight and sum to 0..100,
rounded to four decimals. Missing/zero/unknown data contributes zero, without
renormalizing the remaining weights. No single dimension reaches the match
threshold of 60. At least two dimensions must contribute. Equal scores are
ordered by event ID. Duration is the recorded planning duration, which may be
an intake default; it is not a claim that the brief explicitly supplied it.

Limits are constants: **100 candidates, 8 ranked matches, 12 suggestions**.
Internal caller limits are validated; the browser cannot request larger limits.

## Feedback and aggregation

- Prefer final operator-corrected requirements when present, otherwise the
  original structured requirements. Never use raw item notes as instructions.
- `reuse_plan=no`: retain a warning but exclude all quantities from synthesis.
- `plan_fit=too_much`: withhold quantities rather than copying excess.
- `too_little` or `with_changes` without recorded corrections: withhold quantities.
- `too_little` with corrections: use final requirements with an explicit review
  warning. Corrections are not proof that the problem was resolved.
- Missing, unnecessary, additional onsite, or failed item feedback suppresses
  quantities for the mapped capabilities. Unmapped issues suppress the entire
  example's quantities. Unspecific missing/unnecessary/additional feedback also
  suppresses quantities. Notes and affected item labels are not sent to browsers.
- Synthesize a capability only with at least two contributing examples and
  support from at least half the ranked matches. The maximum observed quantity
  must be no more than 125% of the minimum. Use the lower median, with the
  observed range and evidence count visible for review.
- Suggest only departments already present in the current structured draft.
  History cannot introduce a new lighting department into a no-lighting brief.
  Existing capability-level constraints still pass through the authoritative
  planner when the operator recalculates.
- One example is never presented as repeated organizational knowledge. Divergent
  quantities produce no quantity suggestion. There are no automatic removals;
  a lower supported quantity is shown against the current total for review.

Feedback cannot invent an exact missing quantity. This conservative phase offers
repeatable corrected requirements and warnings, not statistical certainty.

## API and security boundary

`POST /api/events/learning/suggestions`, authenticated with `events.plan`:

```json
{
  "features": {
    "departments": ["furniture"],
    "guest_count": 100,
    "duration_minutes": 120,
    "venue_type": "indoor"
  }
}
```

Optional `event_id` must resolve in the authenticated organization. Foreign and
absent IDs receive the same not-found response. Organization, limits, raw briefs,
and unsupported fields are not accepted. Payloads over 8 KiB are rejected, in
addition to the server's existing request-body bound. Invalid types and ranges
receive validation errors. Organization comes exclusively from session context.

The response contains `suggestions` (capability, amount, minimum, maximum,
evidence_count, reason), aggregate `evidence_count`, `dimensions`, and generic
`warnings`. It does not expose historical IDs, actor identities, titles, notes,
locations, or original briefs. Internal ranking retains IDs, scoring reasons,
final quantities and selected feedback classifications for deterministic tests.
All text remains UTF-8; normalized codes are not translated or scored against
English keywords. Existing item labels and multilingual briefs remain unchanged.

There is no write transaction, allocation call, audit write, or read lock in the
suggestion service. One joined SELECT sees a committed snapshot of eligibility,
feedback answers and item feedback, including while another process is writing.
Existing Phase C writes remain authoritative. `events.learning.manage` is unchanged.

Two concrete D integration corrections: the Phase C examples query now rechecks
eligibility invariants in one snapshot instead of N+1 reads, and the PostgreSQL
legacy raw-text history shortcut returns no implicit requirements. Phase C's
source classification remains unchanged; Phase D neither infers source from text
nor relabels records based on runtime environment. Owners must exclude any
synthetic records incorrectly labeled real before relying on their history.
Test fixtures explicitly set provenance. No schema migration is needed.

## Operator control

The compact **From your event history** section appears only after an explicit
plan build, not on each brief keystroke. Retrieval is debounced by 300 ms; stale
responses are ignored. With no repeatable evidence it shows a normal empty state.

The operator sees current quantities, suggested editable totals, observed ranges,
counts and an explanation of the comparison. Ignore does not change the draft;
Review reopens the suggestions. Apply copies the visible quantities into the
manual requirement editor as required totals, preserving unrelated requirements.
Keyboard focus moves to the editor. The draft is marked dirty and cannot be
saved until explicit recalculation. Normal `/api/events/describe` and
`/api/events/save` remain responsible for planning and server-owned creation.
Suggestions never allocate, reserve, pack, dispatch, or return stock.

## Performance and verification

One bounded candidate SELECT; no full event repository scan and no N+1 queries.
Phase C records are retained internally, with at most 100 feedback items per
record under its existing validation contract. Ranking is O(C log C), C <= 100;
synthesis processes at most eight examples. No cache or additional service.

Tests live in `test_event_similarity.py`, the learning-prefixed PostgreSQL HTTP
tests in `test_postgres_api.py`, and the Phase D browser case in
`tests/e2e/operations.spec.ts`. PostgreSQL fixtures own and remove every inserted
row. Tests inspect all database tables for hidden mutations and run two real API
processes during deliberately uncommitted feedback/eligibility/return changes.
Canonical acceptance must run with PostgreSQL available and zero skipped tests.

Verified locally on 2026-09-11: 214 Python tests, including 42 PostgreSQL-required
and 18 race-required tests, zero skips/failures. The final run used a fresh
PostgreSQL 16.15 cluster after the previous temporary installation lost files.
Playwright passed 65 cases across 360/768/1280/1440 pixels and the 200% equivalent,
including Phase D keyboard controls, large text, and control clipping assertions.
Screenshot inspection found and corrected desktop planner clipping using
container-aware grid sizing. TypeScript, Vite, Alembic check, and an isolated
upgrade/downgrade/re-upgrade migration round trip passed. Browser fixtures use
SQLite through the SQL runtime; storage/isolation/race acceptance uses real
PostgreSQL and two HTTP API processes, not a mocked database.

## Phase E boundary

Possible later work: measure suggestion usefulness, improve normalized venue and
capability coverage, add carefully tested quantity scaling or finer repeated
correction explanations, and improve bounded candidate selection when real
history exceeds 100 events. None is implemented here. Embeddings, models,
external APIs, autonomous planning and cross-customer learning remain out of scope.
