"""Separate logistics reservation windows from actual show time."""
from alembic import op
import sqlalchemy as sa

revision = "0016_logistics_windows"
down_revision = "0015_field_adjustments"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("events", sa.Column("reservation_starts_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("events", sa.Column("reservation_ends_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_events_org_reservation_window", "events", ["organization_id", "reservation_starts_at", "reservation_ends_at"])


def downgrade():
    op.drop_index("ix_events_org_reservation_window", table_name="events")
    op.drop_column("events", "reservation_ends_at")
    op.drop_column("events", "reservation_starts_at")
