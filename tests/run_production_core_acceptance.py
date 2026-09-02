from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

from acceptance_support import (
    POSTGRES_REQUIRED_ATTRIBUTE,
    POSTGRES_TEST_URL_ENV,
    RACE_REQUIRED_ATTRIBUTE,
)


ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
REQUIRE_POSTGRES_ENV = "SALAMANDRA_REQUIRE_POSTGRES_TESTS"


def iter_tests(suite: unittest.TestSuite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from iter_tests(item)
        else:
            yield item


def has_marker(test: unittest.TestCase, marker: str) -> bool:
    method = getattr(test, test._testMethodName)
    return bool(getattr(type(test), marker, False) or getattr(method, marker, False))


class AcceptanceResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.started_test_ids: set[str] = set()

    def startTest(self, test):
        self.started_test_ids.add(test.id())
        super().startTest(test)


def write_summary(lines: list[str]) -> None:
    print("\nProduction core acceptance summary")
    for line in lines:
        print(line)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write("## Production core acceptance\n\n")
            summary.writelines(f"- {line}\n" for line in lines)


def main() -> int:
    require_postgres = os.environ.get(REQUIRE_POSTGRES_ENV) == "1"
    database_url = os.environ.get(POSTGRES_TEST_URL_ENV)
    if require_postgres and not database_url:
        write_summary(
            [
                "Total tests: 0 (test discovery was not started)",
                "PostgreSQL tests executed: 0",
                "Race tests executed: 0",
                "Skipped tests: 0",
                "Failures: 1 (SALAMANDRA_TEST_POSTGRES_URL is required)",
            ]
        )
        return 2

    loader = unittest.TestLoader()
    suite = loader.discover(str(TESTS), pattern="test*.py")
    discovered = list(iter_tests(suite))
    postgres_ids = {
        test.id()
        for test in discovered
        if has_marker(test, POSTGRES_REQUIRED_ATTRIBUTE)
    }
    race_ids = {
        test.id()
        for test in discovered
        if has_marker(test, RACE_REQUIRED_ATTRIBUTE)
    }

    runner = unittest.TextTestRunner(
        verbosity=2,
        resultclass=AcceptanceResult,
    )
    result: AcceptanceResult = runner.run(suite)
    skipped_ids = {test.id() for test, _ in result.skipped}
    postgres_executed = postgres_ids.intersection(result.started_test_ids) - skipped_ids
    race_executed = race_ids.intersection(result.started_test_ids) - skipped_ids
    missing_postgres = postgres_ids - postgres_executed
    missing_races = race_ids - race_executed
    failure_count = len(result.failures) + len(result.errors)

    lines = [
        f"Total tests: {result.testsRun}/{len(discovered)} executed",
        (
            "PostgreSQL tests executed: "
            f"{len(postgres_executed)}/{len(postgres_ids)} required"
        ),
        f"Race tests executed: {len(race_executed)}/{len(race_ids)} required",
        f"Skipped tests: {len(result.skipped)}",
        f"Failures: {failure_count}",
    ]
    if missing_postgres:
        lines.append("Missing PostgreSQL tests: " + ", ".join(sorted(missing_postgres)))
    if missing_races:
        lines.append("Missing race tests: " + ", ".join(sorted(missing_races)))
    write_summary(lines)

    if not result.wasSuccessful():
        return 1
    if require_postgres and (missing_postgres or missing_races):
        return 2
    if require_postgres and not postgres_ids:
        return 2
    if require_postgres and not race_ids:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
