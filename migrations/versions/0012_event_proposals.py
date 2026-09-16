"""Immutable server-generated proposal evidence before initial event save."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0012_event_proposals"
down_revision = "0011_suggestion_outcomes"
branch_labels = None
depends_on = None


def upgrade():
    value = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.create_table("event_proposals",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.String(64), nullable=False),
        sa.Column("actor_user_id", sa.String(64), nullable=False),
        sa.Column("original_request", value, nullable=False),
        sa.Column("proposal", value, nullable=False),
        sa.Column("consumed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id", "actor_user_id"],
            ["memberships.organization_id", "memberships.user_id"], ondelete="RESTRICT",
            name="fk_event_proposal_actor_org"))
    op.create_index("ix_event_proposals_org_actor", "event_proposals", ["organization_id", "actor_user_id"])


def downgrade():
    op.drop_index("ix_event_proposals_org_actor", table_name="event_proposals")
    op.drop_table("event_proposals")
