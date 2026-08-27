# Salamandra Agent Instructions

## Product Direction

Salamandra is a warehouse/event manager for in-house use first. The system should stay clean, fast, and practical, with workflows that follow the real logical order of warehouse and event operations.

Build the source code foundation before the public site experience. When the site work begins, be prepared to use TypeScript and keep the frontend aligned with the source model instead of duplicating business rules.

## Working Rules

- Review the relevant code before making changes.
- Explain the intended change and why it is needed before implementation when the user asks to review first.
- Keep changes small, focused, and easy to review.
- After each PR or proposed PR, show what changed and explain why it changed.
- Do not commit, push, or open a PR without explicit user authorization for that specific action.
- Continue on `main` for now, unless the user asks to branch out later.
- Push only to the GitHub repository provided by the user: `https://github.com/Hmimesh/Salamandra`.

## Engineering Priorities

- Prefer clear domain models over quick UI-driven shortcuts.
- Keep inventory, item, requirement, event, and warehouse logic separated from presentation/UI code.
- Make CLI/UI flows thin wrappers around reusable source logic.
- Avoid hidden state and surprising side effects.
- Normalize identifiers consistently at system boundaries.
- Validate user input close to where it enters the system.
- Favor readable code over clever abstractions.

## Security And Data Integrity Rules

- Treat every client-provided identifier and field as untrusted. Derive actor, organization,
  permissions, event ownership, operational status, allocation plans, and audit fields on the
  server.
- Enforce permissions through `src/security.py`; do not add route-local role checks when a
  named permission applies.
- Scope every tenant-owned lookup by both resource ID and the authenticated organization. Use
  a non-disclosing not-found response for another organization's resource.
- Event creation is create-only and server planned. Existing events change through explicit
  actions and the legal state sequence: planning, confirmed, packed, out, returned.
- Dispatch and return must be idempotent and tied to exact source holdings. Never return stock
  from an aggregate pool without an authoritative dispatch movement.
- JSON persistence is local compatibility storage, not a production concurrency solution.
  Production transactional work targets the PostgreSQL schema in `src/database.py` and Alembic
  migrations in `migrations/`.
- Production startup must never create accounts with known passwords or enable demo sign-in
  without an explicit local-development switch.

## Planning Domain Rules

- Automatic plans must allocate only inventory that exists in the signed-in user's personal inventory or the organization's shared inventory.
- Event descriptions must be parsed into structured facts before equipment is selected: date, milestones, venue, guest count, scale, performers, instruments, exclusions, and operational priority.
- Plan against capabilities such as `pa.main`, `monitor.stage`, and `cable.xlr`, then resolve those needs to real inventory items through `ItemClass` definitions.
- Treat explicit exclusions such as "no lighting" as hard constraints.
- Every selected microphone, main PA speaker, and stage monitor requires at least one XLR cable. Powered equipment must expand its own power requirements. Recommended spares are separate from required stock.
- Rank sufficient substitutes deterministically using capability fit, connector compatibility, event suitability, availability, organization preference, quality, and handling weight. Show operators why a substitute was selected.
- Treat Salamandra as a general event operations manager, not an audio-only tool. Site, furniture, hospitality, video, transport, and organization-defined classes are first-class inventory.
- Never invent a physical item. Every allocation, vehicle, cart, substitute, and spare must map to available personal or shared inventory; missing capability stays visible as a sourcing conflict.
- Consolidate each physical inventory item into one plan and checklist line. Preserve required, recommended, and optional quantities as a breakdown on that line.
- Derive load transport from the actual allocated payload and event scale. Prefer the smallest sufficient stocked vehicle, then use a larger capable vehicle only as a fallback.
- Keep account display preferences scoped to the user and business integrations scoped to the organization. Never persist API keys or OAuth secrets; persist only credential environment-variable names and connection metadata.
- Optimize all overlapping events together. Packed or dispatched work must not be silently disrupted; among planning work, the more difficult or higher-priority event receives the strongest scarce match and lower-priority work receives the best sufficient substitute.
- Saving a reallocation must leave an event-history entry and regenerate affected packing and return checklists.
- Users manage normal equipment behavior through organization-owned configured item classes. Do not require users to create Python subclasses.

## Current Repository Shape

- Python source lives in `src/`.
- Unit tests live in `tests/`.
- Current CLI entrypoint is `src/main.py`.
- Current inventory UI flow is in `src/uiback.py`.
- Current inventory and item models are in `src/Inventory.py` and `src/Item_node.py`.
- Configurable item classes and capability rules are in `src/item_classes.py`.
- Description parsing is in `src/event_intake.py`; weighted allocation is in `src/event_planner.py`.
- The React/TypeScript application lives in `frontend/`; `web/` is its production build output.

## Verification

Run tests before and after code changes when possible:

```powershell
python -m unittest discover -s tests
```

If the local `python` command is unavailable, use the available project or workspace Python runtime and report the exact command used.

If tests already fail before a change, report the baseline failure clearly and separate it from any new result caused by the change.

Keep permanent regression scenarios for the small coffee-house performance, a medium outdoor event, a large festival, and same-day weighted reallocation. Any planner change must keep those scenarios passing.

## Future TypeScript Site Guidance

- Use TypeScript for site code unless the user chooses otherwise.
- Keep shared concepts explicit: items, requirements, stock counts, in-use counts, events, kits, reservations, and warehouse movements.
- Keep UI fast and direct for in-house operators.
- Avoid marketing-first screens for internal tools; start with the actual working interface.
- Use typed API contracts when the frontend starts talking to backend/source logic.
