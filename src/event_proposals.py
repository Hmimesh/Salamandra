"""Server-owned original planning evidence, never client-supplied plan truth."""
from copy import deepcopy

from sqlalchemy import select

from database import EventProposalModel, AuditEventModel, active_membership, new_id
from event_learning import _original, _proposal, _corrections
from security import Permission, ResourceNotFound, StateConflict, require_permission


def capture_proposal(factory, organization_id, actor_id, data, request, key=None, request_id=""):
    from suggestion_outcomes import receipt, complete
    with factory.begin() as session:
        membership = active_membership(session, organization_id, actor_id)
        require_permission(membership.role, Permission.EVENTS_PLAN)
        operation = receipt(session, organization_id, actor_id, "event.proposal", key or new_id(), request)
        if operation.status == "completed":
            return deepcopy(operation.response)
        row = EventProposalModel(organization_id=organization_id, actor_user_id=actor_id,
            original_request=deepcopy(_original(data, request)), proposal=deepcopy(_proposal(data)))
        session.add(row)
        session.flush()
        session.add(AuditEventModel(organization_id=organization_id,
            actor_membership_id=membership.id, action="event.proposal.created",
            resource_type="event_proposal", resource_id=row.id, request_id=request_id,
            changes={"proposal_id": row.id}))
        return complete(operation, {"proposal_id": row.id, "event": deepcopy(data)})


def consume_proposal(session, learning, event, actor_id, identity):
    if not isinstance(identity, str) or not 1 <= len(identity) <= 64 or not all(c.isascii() and (c.isalnum() or c in "-_") for c in identity):
        raise ValueError("Proposal reference is invalid.")
    row = session.scalar(select(EventProposalModel).where(
        EventProposalModel.id == identity,
        EventProposalModel.organization_id == event.organization_id,
        EventProposalModel.actor_user_id == actor_id).with_for_update())
    if row is None:
        raise ResourceNotFound("Proposal was not found.")
    if row.consumed:
        raise StateConflict("This proposal was already used. Build a new event plan.")
    learning.original_request = deepcopy(row.original_request)
    learning.proposal = deepcopy(row.proposal)
    learning.corrections = _corrections(row.proposal, event.data)
    row.consumed = True
