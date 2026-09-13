# Event Learning Phase C

Phase C stores evidence for future planning without making recommendations. Each PostgreSQL event has one organization-scoped `event_learning_records` row. It preserves the original brief and request context, normalized features, the system proposal, and an operator correction delta. Execution is refreshed from the existing allocation and stock-movement ledger at each lifecycle transition; it does not create a second inventory history.

Returned events may receive one optional versioned `event_feedback` record and structured affected-item rows. Review answers are `yes`/`no`, plan fit, plan reuse, optional quantities, and UTF-8 notes. A review never blocks return. Retry keys are organization-scoped through `operation_requests`; identical retries replay, conflicting payloads return 409, and stale feedback versions are rejected.

Only returned, real-source events become eligible by default. Cancelled, incomplete, synthetic, demo, imported-unknown, and explicitly excluded events remain out. Owners and administrators control eligibility through the centralized `events.learning.manage` permission. Historical records are never rewritten when an item is renamed or archived; affected-item rows retain the stable item ID and a compact label snapshot.

The bounded Phase D contract is `EventLearningStore.examples(organization_id, limit)`: it returns event identity, features, final proposal/requirements, ledger-derived execution, feedback, source, and eligibility. It performs no similarity scoring, weighting, recommendation, model training, embedding, or cross-organization aggregation. Retrieval is organization- and eligibility-indexed with a bounded limit; event pages do not aggregate the history table.

## Review Sessions And Eligibility Concurrency

Every opened review is an event-keyed form session. Saving is blocked until its read completes successfully; late responses from closed sessions are ignored. Saved affected-item rows round-trip independently, including repeated issue types, null quantities, unresolved item snapshots, and Unicode notes.

`GET /api/events/learning?event_id=...` exposes the learning record's `version`.
`POST /api/events/learning/eligibility` requires `event_id`, `eligible`,
`learning_version` (a positive integer from that read), and `idempotency_key`;
`reason` is optional. `event_version` is not a substitute for `learning_version`.
There is currently no frontend eligibility-management control to update.

The transaction authorizes the active owner/admin membership, locks the scoped event
first (consistent with lifecycle lock ordering), checks the organization-scoped
operation receipt, then locks and compares the learning record. A stale request
returns 409 without audit or operation writes. A successful decision increments the
learning version once and stores its committed response with its audit row in the
same transaction. An identical idempotent replay returns that stored response even
after later decisions; conflicting key reuse returns 409. Missing records must be
reloaded, not silently created by an eligibility decision.

Cancellation synchronizes execution inside the existing event transaction after
flushing authoritative movements. Planning, confirmed, and packed cancellations
record terminal status `cancelled`, preserve prior packing evidence, and exclude
the record from historical suggestions. Cancellation never fabricates a return.
