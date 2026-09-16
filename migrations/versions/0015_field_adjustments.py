"""Auditable field adjustments and retained released allocation lines."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0015_field_adjustments"
down_revision = "0014_condition_event_origin"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("field_adjustments",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.String(64), nullable=False),
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("actor_membership_id", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("data", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["event_id", "organization_id"], ["events.id", "events.organization_id"], name="fk_adjustment_event_org", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_membership_id", "organization_id"], ["memberships.id", "memberships.organization_id"], name="fk_adjustment_actor_org", ondelete="RESTRICT"),
        sa.CheckConstraint("status IN ('pending','fulfilled','cancelled')", name="ck_adjustment_status"),
        sa.CheckConstraint("version > 0", name="ck_adjustment_version"))
    op.create_index("ix_adjustment_event", "field_adjustments", ["organization_id", "event_id", "created_at"])
    with op.batch_alter_table("allocation_lines") as batch:
        batch.drop_constraint("ck_allocation_lines_state", type_="check")
        batch.create_check_constraint("ck_allocation_lines_state", "state IN ('reserved','packed','dispatched','returned','released')")


def downgrade():
    # Never silently discard physical history to fit an older schema.
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM allocation_lines WHERE state = 'released'")).scalar():
        raise RuntimeError("Cannot downgrade while retained released allocation lines exist.")
    with op.batch_alter_table("allocation_lines") as batch:
        batch.drop_constraint("ck_allocation_lines_state", type_="check")
        batch.create_check_constraint("ck_allocation_lines_state", "state IN ('reserved','packed','dispatched','returned')")
    op.drop_index("ix_adjustment_event", table_name="field_adjustments")
    op.drop_table("field_adjustments")
