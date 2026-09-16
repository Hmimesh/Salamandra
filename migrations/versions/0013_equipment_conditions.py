"""Quantity-based condition incidents and immutable condition movements."""
from alembic import op
import sqlalchemy as sa

revision = "0013_equipment_conditions"
down_revision = "0012_event_proposals"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("inventory_holdings") as batch:
        batch.add_column(sa.Column("condition_quantity", sa.Integer(), server_default="0", nullable=False))
        batch.create_check_constraint("ck_holdings_condition", "condition_quantity >= 0")
    op.create_table("condition_incidents",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.String(64), nullable=False),
        sa.Column("holding_id", sa.String(64), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("issue", sa.String(1000), nullable=False),
        sa.Column("resolution", sa.String(1000), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("id", "organization_id", name="uq_condition_incident_org"),
        sa.ForeignKeyConstraint(["holding_id", "organization_id"], ["inventory_holdings.id", "inventory_holdings.organization_id"], ondelete="RESTRICT", name="fk_condition_holding_org"),
        sa.CheckConstraint("quantity > 0", name="ck_condition_quantity"),
        sa.CheckConstraint("version > 0", name="ck_condition_version"),
        sa.CheckConstraint("status IN ('ready','needs_repair','in_repair','quarantine','missing','retired')", name="ck_condition_status"))
    op.create_index("ix_condition_org_status", "condition_incidents", ["organization_id", "status", "created_at"])
    op.create_table("condition_movements",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.String(64), nullable=False),
        sa.Column("incident_id", sa.String(64), nullable=False),
        sa.Column("actor_membership_id", sa.String(64), nullable=False),
        sa.Column("from_state", sa.String(32), nullable=False),
        sa.Column("to_state", sa.String(32), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("available_before", sa.Integer(), nullable=False),
        sa.Column("available_after", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["incident_id", "organization_id"], ["condition_incidents.id", "condition_incidents.organization_id"], ondelete="RESTRICT", name="fk_condition_movement_incident_org"),
        sa.ForeignKeyConstraint(["actor_membership_id", "organization_id"], ["memberships.id", "memberships.organization_id"], ondelete="RESTRICT", name="fk_condition_movement_actor_org"),
        sa.UniqueConstraint("organization_id", "idempotency_key", name="uq_condition_movement_key"),
        sa.CheckConstraint("quantity > 0 AND available_before >= 0 AND available_after >= 0", name="ck_condition_movement_quantities"))
    op.create_index("ix_condition_movement_incident", "condition_movements", ["organization_id", "incident_id", "created_at"])


def downgrade():
    op.drop_table("condition_movements")
    op.drop_table("condition_incidents")
    with op.batch_alter_table("inventory_holdings") as batch:
        batch.drop_constraint("ck_holdings_condition", type_="check")
        batch.drop_column("condition_quantity")
