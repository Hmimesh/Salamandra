"""Create Salamandra transactional core schema.

Revision ID: 0001_transactional_core
Revises:
Create Date: 2026-08-21
"""
from __future__ import annotations

import sys
from pathlib import Path

from alembic import op


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from database import Base


revision = "0001_transactional_core"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=False)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
