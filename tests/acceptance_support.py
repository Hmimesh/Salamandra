from __future__ import annotations

import os
import unittest
from collections.abc import Callable
from typing import TypeVar


POSTGRES_REQUIRED_ATTRIBUTE = "__salamandra_postgres_required__"
RACE_REQUIRED_ATTRIBUTE = "__salamandra_race_required__"
POSTGRES_TEST_URL_ENV = "SALAMANDRA_TEST_POSTGRES_URL"

TestTarget = TypeVar("TestTarget")


def postgres_required(target: TestTarget) -> TestTarget:
    setattr(target, POSTGRES_REQUIRED_ATTRIBUTE, True)
    return unittest.skipUnless(
        bool(os.environ.get(POSTGRES_TEST_URL_ENV)),
        "Requires the disposable PostgreSQL service configured for acceptance tests.",
    )(target)


def race_required(test: Callable) -> Callable:
    setattr(test, RACE_REQUIRED_ATTRIBUTE, True)
    return test
