"""Durable suggestion snapshots and versioned returned comparisons."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011_suggestion_outcomes"
down_revision = "0010_learning_candidates"
branch_labels = None
depends_on = None
JSON_VALUE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade():
    op.create_table("suggestion_sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.String(64), nullable=False),
        sa.Column("actor_user_id", sa.String(64), nullable=False),
        sa.Column("event_id", sa.String(64)),
        sa.Column("event_id_snapshot", sa.String(64)),
        sa.Column("event_title_snapshot", sa.String(300), nullable=False, server_default=""),
        sa.Column("event_version", sa.Integer()),
        sa.Column("algorithm_version", sa.String(32), nullable=False),
        sa.Column("retrieval_version", sa.String(32), nullable=False),
        sa.Column("evidence_count", sa.Integer(), nullable=False),
        sa.Column("snapshot", JSON_VALUE, nullable=False),
        sa.Column("action", sa.String(32)),
        sa.Column("applied", JSON_VALUE, nullable=False),
        sa.Column("committed", JSON_VALUE, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("id", "organization_id", name="uq_suggestion_session_org"),
        sa.ForeignKeyConstraint(["event_id", "organization_id"], ["events.id", "events.organization_id"], name="fk_suggestion_event_org", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "actor_user_id"], ["memberships.organization_id", "memberships.user_id"], name="fk_suggestion_actor_org", ondelete="RESTRICT"),
        sa.CheckConstraint("action IS NULL OR action IN ('ignored', 'applied', 'applied_with_edits')", name="ck_suggestion_action"),
        sa.CheckConstraint("evidence_count BETWEEN 0 AND 8", name="ck_suggestion_evidence"))
    op.create_index("ix_suggestion_org_action", "suggestion_sessions", ["organization_id", "action", "created_at"])
    op.create_index("ix_suggestion_org_event", "suggestion_sessions", ["organization_id", "event_id"])
    op.create_table("suggestion_evaluations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.String(64), nullable=False),
        sa.Column("suggestion_session_id", sa.String(64), nullable=False),
        sa.Column("learning_version", sa.Integer(), nullable=False),
        sa.Column("feedback_version", sa.Integer(), nullable=False),
        sa.Column("comparisons", JSON_VALUE, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["suggestion_session_id", "organization_id"], ["suggestion_sessions.id", "suggestion_sessions.organization_id"], name="fk_suggestion_evaluation_org", ondelete="CASCADE"),
        sa.UniqueConstraint("suggestion_session_id", "learning_version", "feedback_version", name="uq_suggestion_evaluation_version"))
    op.create_index("ix_suggestion_evaluation_org", "suggestion_evaluations", ["organization_id", "suggestion_session_id", "created_at"])


def downgrade():
    op.drop_table("suggestion_evaluations")
    op.drop_table("suggestion_sessions")
