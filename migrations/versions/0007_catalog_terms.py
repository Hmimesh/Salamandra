"""Organization-owned canonical category labels and confirmed aliases."""
from alembic import op
import sqlalchemy as sa

revision = "0007_catalog_terms"
down_revision = "0006_staging_usability"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "catalog_terms",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("organization_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("normalized_label", sa.String(256), nullable=False),
        sa.Column("canonical_code", sa.String(80), nullable=False),
        sa.Column("language", sa.String(32), nullable=False),
        sa.Column("confirmed_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["confirmed_by", "organization_id"],
                                ["memberships.id", "memberships.organization_id"],
                                ondelete="RESTRICT", name="fk_catalog_terms_actor_org"),
        sa.UniqueConstraint("organization_id", "normalized_label", name="uq_catalog_terms_org_label"),
        sa.CheckConstraint("kind IN ('category', 'alias')", name="ck_catalog_terms_kind"),
    )


def downgrade():
    op.drop_table("catalog_terms")
