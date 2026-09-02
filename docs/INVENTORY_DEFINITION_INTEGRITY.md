# Inventory Definition Integrity

PostgreSQL inventory holdings are the stable physical identity referenced by
allocation lines, stock movements, adjustments, and audit history. Ordinary
editing must not rewrite that identity while physical stock is reserved, packed,
or dispatched.

## Immutable While Operationally Active

When `reserved_quantity`, `packed_quantity`, or `dispatched_quantity` is nonzero,
ordinary editing rejects changes to:

- item identity (`legacy_item_id` / API item ID)
- item type, class, preset-derived definition, manufacturer, and model
- condition
- capabilities and connectors
- linked requirements
- capability/substitution attributes
- quality and organization preference scores
- handling weight
- any unknown definition metadata field

These fields affect allocation identity, capability matching, substitution,
handling, or the operator's understanding of what was dispatched. The holding,
allocation lines, event, movement rows, quantities, and existing audits remain
unchanged when a protected edit is rejected.

## Safely Editable While Active

`info` is a descriptive operator note. It may change while stock is active because
it does not alter allocation identity or capability matching. A real change still
creates an `inventory.definition_updated` audit record. Sending the same value is
a no-op and creates no audit record.

## Separate Commands

The definition-update command never accepts changes to:

- available, reserved, packed, dispatched, count, or in-use quantities
- shared/personal scope or personal owner
- active/archive state
- holding version

Stock quantities move only through transactional inventory adjustments or event
allocation movements. Scope and ownership transfer require a future explicit
administrative workflow; ordinary editing does not provide one. Archival and
reactivation remain consequences of authoritative stock adjustment commands.

## Transaction And Audit Model

`TransactionalInventoryOperations.update_definition` resolves the authenticated
organization and actor membership, enforces the centralized
`inventory.definition.manage` permission, acquires organization-scoped
idempotency, locks the target holding, validates active buckets, checks identity
collisions, applies permitted changes, and writes the audit in one transaction.

The audit identifies the organization, actor membership, holding, request,
timestamp, changed field names, and the relevant before/after values. Historical
allocation and movement rows are never rewritten to match a later definition.

Stock-add, preset-add, reactivation, and CSV reconciliation use the same protected
field comparison before changing metadata on an existing holding. This prevents
those routes from bypassing the ordinary-edit invariant.
