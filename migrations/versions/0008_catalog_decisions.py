"""Workspace-scoped, bounded product review decisions."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008_catalog_decisions"
down_revision = "0007_catalog_terms"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "catalog_decisions",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("organization_id", sa.String(64), nullable=False),
        sa.Column("pair_key", sa.String(64), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("data", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("organization_id", "pair_key", name="uq_catalog_decisions_org_pair"),
        sa.CheckConstraint("action IN ('separate', 'same')", name="ck_catalog_decisions_action"),
    )


def downgrade():
    op.drop_table("catalog_decisions")
