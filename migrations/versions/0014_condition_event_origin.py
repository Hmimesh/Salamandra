"""Preserve the event origin of damaged and missing return incidents."""
from alembic import op
import sqlalchemy as sa

revision = "0014_condition_event_origin"
down_revision = "0013_equipment_conditions"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("condition_incidents") as batch:
        batch.add_column(sa.Column("event_id", sa.String(64), nullable=True))
        batch.create_foreign_key("fk_condition_event_org", "events", ["event_id", "organization_id"], ["id", "organization_id"], ondelete="RESTRICT")


def downgrade():
    with op.batch_alter_table("condition_incidents") as batch:
        batch.drop_constraint("fk_condition_event_org", type_="foreignkey")
        batch.drop_column("event_id")
