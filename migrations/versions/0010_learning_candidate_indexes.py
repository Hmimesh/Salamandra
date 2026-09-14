"""Index structured learning candidate retrieval; no historical evidence changes."""
from alembic import op

revision = "0010_learning_candidates"
down_revision = "0009_event_learning"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_learning_candidates_recent", "event_learning_records",
                    ["organization_id", "eligible", "created_at", "event_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.create_index("ix_learning_candidates_features", "event_learning_records", ["features"],
                        postgresql_using="gin", postgresql_ops={"features": "jsonb_path_ops"})


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.drop_index("ix_learning_candidates_features", table_name="event_learning_records")
    op.drop_index("ix_learning_candidates_recent", table_name="event_learning_records")
