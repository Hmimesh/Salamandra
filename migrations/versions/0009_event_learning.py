"""Add organization-scoped event learning and feedback records."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0009_event_learning"
down_revision = "0008_catalog_decisions"
branch_labels = None
depends_on = None
JSON_VALUE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade():
    op.create_table(
        "event_learning_records",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("organization_id", sa.String(64), nullable=False),
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False, server_default="real"),
        sa.Column("source_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("eligible", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("exclusion_reason", sa.String(80), nullable=True),
        sa.Column("features", JSON_VALUE, nullable=False),
        sa.Column("original_request", JSON_VALUE, nullable=False),
        sa.Column("proposal", JSON_VALUE, nullable=False),
        sa.Column("corrections", JSON_VALUE, nullable=False),
        sa.Column("execution", JSON_VALUE, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_learning_records_id_org"),
        sa.ForeignKeyConstraint(["event_id", "organization_id"], ["events.id", "events.organization_id"], ondelete="CASCADE", name="fk_learning_records_event_same_org"),
        sa.CheckConstraint("version > 0", name="ck_learning_records_version"),
    )
    op.create_index("ix_learning_records_org_eligible", "event_learning_records", ["organization_id", "eligible", "event_id"])
    op.create_table(
        "event_feedback",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("organization_id", sa.String(64), nullable=False),
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("missing", sa.String(8), nullable=False),
        sa.Column("unnecessary", sa.String(8), nullable=False),
        sa.Column("failed", sa.String(8), nullable=False),
        sa.Column("additional_onsite", sa.String(8), nullable=False),
        sa.Column("plan_fit", sa.String(24), nullable=False),
        sa.Column("reuse_plan", sa.String(24), nullable=False),
        sa.Column("notes", sa.String(2000), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_event_feedback_id_org"),
        sa.ForeignKeyConstraint(["event_id", "organization_id"], ["events.id", "events.organization_id"], ondelete="CASCADE", name="fk_event_feedback_event_same_org"),
        sa.CheckConstraint("missing IN ('yes', 'no')", name="ck_event_feedback_missing"),
        sa.CheckConstraint("unnecessary IN ('yes', 'no')", name="ck_event_feedback_unnecessary"),
        sa.CheckConstraint("failed IN ('yes', 'no')", name="ck_event_feedback_failed"),
        sa.CheckConstraint("additional_onsite IN ('yes', 'no')", name="ck_event_feedback_additional"),
        sa.CheckConstraint("plan_fit IN ('too_little', 'about_right', 'too_much')", name="ck_event_feedback_plan_fit"),
        sa.CheckConstraint("reuse_plan IN ('yes', 'with_changes', 'no')", name="ck_event_feedback_reuse"),
        sa.CheckConstraint("version > 0", name="ck_event_feedback_version"),
    )
    op.create_index("ix_event_feedback_org_event", "event_feedback", ["organization_id", "event_id"])
    op.create_table(
        "event_feedback_items",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("organization_id", sa.String(64), nullable=False),
        sa.Column("feedback_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("item_id", sa.String(256), nullable=True),
        sa.Column("label_snapshot", sa.String(300), nullable=False, server_default=""),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(500), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["feedback_id", "organization_id"], ["event_feedback.id", "event_feedback.organization_id"], ondelete="CASCADE", name="fk_event_feedback_items_feedback_same_org"),
        sa.CheckConstraint("kind IN ('missing', 'unnecessary', 'failed', 'additional_onsite')", name="ck_event_feedback_items_kind"),
        sa.CheckConstraint("quantity IS NULL OR quantity > 0", name="ck_event_feedback_items_quantity"),
    )
    op.create_index("ix_event_feedback_items_org_feedback", "event_feedback_items", ["organization_id", "feedback_id"])


def downgrade():
    op.drop_index("ix_event_feedback_items_org_feedback", table_name="event_feedback_items")
    op.drop_table("event_feedback_items")
    op.drop_index("ix_event_feedback_org_event", table_name="event_feedback")
    op.drop_table("event_feedback")
    op.drop_index("ix_learning_records_org_eligible", table_name="event_learning_records")
    op.drop_table("event_learning_records")
