"""Organization-scoped crew, skills, roles, assignments and hashed access."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0017_event_crew"
down_revision = "0016_logistics_windows"
branch_labels = None
depends_on = None
JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade():
    op.create_table("crew_profiles",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.String(64), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("complexity", sa.Integer(), nullable=False),
        sa.Column("contact", sa.String(400), nullable=False),
        sa.Column("notes", sa.String(2000), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("id", "organization_id", name="uq_crew_profile_org"),
        sa.CheckConstraint("kind IN ('internal','external')", name="ck_crew_kind"),
        sa.CheckConstraint("complexity BETWEEN 1 AND 3", name="ck_crew_complexity"),
        sa.CheckConstraint("version > 0", name="ck_crew_version"))
    op.create_index("ix_crew_org", "crew_profiles", ["organization_id", "name", "id"])
    op.create_table("crew_skills",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("profile_id", sa.String(64), nullable=False),
        sa.Column("organization_id", sa.String(64), nullable=False),
        sa.Column("skill", sa.String(100), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.UniqueConstraint("profile_id", "skill", name="uq_crew_skill_profile"),
        sa.ForeignKeyConstraint(["profile_id", "organization_id"], ["crew_profiles.id", "crew_profiles.organization_id"], ondelete="RESTRICT", name="fk_crew_skill_org"),
        sa.CheckConstraint("level BETWEEN 1 AND 3", name="ck_crew_skill_level"))
    op.create_table("crew_roles",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.String(64), nullable=False),
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("complexity", sa.Integer(), nullable=False),
        sa.Column("skills", JSON, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("id", "event_id", "organization_id", name="uq_crew_role_event_org"),
        sa.ForeignKeyConstraint(["event_id", "organization_id"], ["events.id", "events.organization_id"], ondelete="RESTRICT", name="fk_crew_role_event"),
        sa.CheckConstraint("quantity BETWEEN 1 AND 100", name="ck_crew_role_quantity"),
        sa.CheckConstraint("complexity BETWEEN 1 AND 3", name="ck_crew_role_complexity"),
        sa.CheckConstraint("version > 0", name="ck_crew_role_version"))
    op.create_index("ix_crew_role_event", "crew_roles", ["organization_id", "event_id"])
    op.create_table("crew_assignments",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.String(64), nullable=False),
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("role_id", sa.String(64), nullable=False),
        sa.Column("profile_id", sa.String(64), nullable=False),
        sa.Column("call_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("release_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("notes", sa.String(2000), nullable=False),
        sa.Column("equipment", JSON, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updates", JSON, nullable=False),
        sa.UniqueConstraint("id", "organization_id", name="uq_crew_assignment_org"),
        sa.ForeignKeyConstraint(["role_id", "event_id", "organization_id"], ["crew_roles.id", "crew_roles.event_id", "crew_roles.organization_id"], ondelete="RESTRICT", name="fk_crew_assignment_role"),
        sa.ForeignKeyConstraint(["profile_id", "organization_id"], ["crew_profiles.id", "crew_profiles.organization_id"], ondelete="RESTRICT", name="fk_crew_assignment_profile"),
        sa.CheckConstraint("release_at > call_at", name="ck_crew_assignment_window"),
        sa.CheckConstraint("status IN ('assigned','cancelled')", name="ck_crew_assignment_status"),
        sa.CheckConstraint("version > 0", name="ck_crew_assignment_version"))
    op.create_index("ix_crew_assignment_window", "crew_assignments", ["organization_id", "profile_id", "call_at", "release_at"])
    op.create_index("ix_crew_assignment_event", "crew_assignments", ["organization_id", "event_id"])
    op.create_table("crew_access",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.String(64), nullable=False),
        sa.Column("assignment_id", sa.String(64), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["assignment_id", "organization_id"], ["crew_assignments.id", "crew_assignments.organization_id"], ondelete="RESTRICT", name="fk_crew_access_assignment"))
    op.create_index("ix_crew_access_assignment", "crew_access", ["organization_id", "assignment_id"])


def downgrade():
    op.drop_table("crew_access")
    op.drop_table("crew_assignments")
    op.drop_table("crew_roles")
    op.drop_table("crew_skills")
    op.drop_table("crew_profiles")
