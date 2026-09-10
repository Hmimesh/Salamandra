# Event Learning Phase C

Phase C stores evidence for future planning without making recommendations. Each PostgreSQL event has one organization-scoped `event_learning_records` row. It preserves the original brief and request context, normalized features, the system proposal, and an operator correction delta. Execution is refreshed from the existing allocation and stock-movement ledger at each lifecycle transition; it does not create a second inventory history.

Returned events may receive one optional versioned `event_feedback` record and structured affected-item rows. Review answers are `yes`/`no`, plan fit, plan reuse, optional quantities, and UTF-8 notes. A review never blocks return. Retry keys are organization-scoped through `operation_requests`; identical retries replay, conflicting payloads return 409, and stale feedback versions are rejected.

Only returned, real-source events become eligible by default. Cancelled, incomplete, synthetic, demo, imported-unknown, and explicitly excluded events remain out. Owners and administrators control eligibility through the centralized `events.learning.manage` permission. Historical records are never rewritten when an item is renamed or archived; affected-item rows retain the stable item ID and a compact label snapshot.

The bounded Phase D contract is `EventLearningStore.examples(organization_id, limit)`: it returns event identity, features, final proposal/requirements, ledger-derived execution, feedback, source, and eligibility. It performs no similarity scoring, weighting, recommendation, model training, embedding, or cross-organization aggregation. Retrieval is organization- and eligibility-indexed with a bounded limit; event pages do not aggregate the history table.
