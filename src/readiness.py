from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Engine


@dataclass(frozen=True)
class ReadinessResult:
    ready: bool
    code: str


class DatabaseReadiness:
    def __init__(self, engine: Engine, project_root: Path):
        self.engine = engine
        config = Config(str(project_root / "alembic.ini"))
        config.set_main_option("script_location", str(project_root / "migrations"))
        self.expected_revisions = frozenset(ScriptDirectory.from_config(config).get_heads())

    def check(self) -> ReadinessResult:
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
                current_revisions = frozenset(
                    MigrationContext.configure(connection).get_current_heads()
                )
        except Exception:
            return ReadinessResult(False, "database_unavailable")
        if not current_revisions or current_revisions != self.expected_revisions:
            return ReadinessResult(False, "schema_mismatch")
        return ReadinessResult(True, "ready")

    def require_ready(self) -> None:
        result = self.check()
        if not result.ready:
            raise RuntimeError(
                "PostgreSQL is unavailable or its Alembic schema revision is not current."
            )
