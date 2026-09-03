"""Protect normalized workspace registration emails.

Revision ID: 0005_workspace_registration
Revises: 0004_inventory_ledger
Create Date: 2026-09-03
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005_workspace_registration"
down_revision = "0004_inventory_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_users_email_normalized",
        "users",
        [sa.text("lower(email)")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_users_email_normalized", table_name="users")
