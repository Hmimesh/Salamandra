from __future__ import annotations

import io
import json
import logging
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TESTS = ROOT / "tests"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from operational_logging import JsonLogFormatter, log_event
from readiness import ReadinessResult
from server import SalamandraServer
from test_security_phase1 import ServerHarness
from web_config import ConfigurationError, WebConfig


STAGING_ORIGIN = "https://salamandra-staging.hmimesh.com"


def staging_environment(**overrides: str) -> dict[str, str]:
    values = {
        "SALAMANDRA_ENV": "staging",
        "SALAMANDRA_DATABASE_URL": (
            "postgresql+psycopg://app:example-only@db.internal/salamandra"
        ),
        "SALAMANDRA_APP_ORIGIN": STAGING_ORIGIN,
        "SALAMANDRA_ALLOWED_ORIGINS": STAGING_ORIGIN,
        "SALAMANDRA_TRUSTED_HOSTS": "salamandra-staging.hmimesh.com",
        "SALAMANDRA_COOKIE_SECURE": "1",
        "SALAMANDRA_SESSION_MAX_AGE_SECONDS": "3600",
        "SALAMANDRA_LOG_LEVEL": "INFO",
    }
    values.update(overrides)
    return values


class FakeReadiness:
    def __init__(self, result: ReadinessResult):
        self.result = result

    def check(self) -> ReadinessResult:
        return self.result


class TestWebConfiguration(unittest.TestCase):
    def test_insecure_staging_cookie_configuration_is_rejected(self):
        with self.assertRaises(ConfigurationError):
            WebConfig.from_environment(
                staging_environment(SALAMANDRA_COOKIE_SECURE="0")
            )

    def test_invalid_and_insecure_staging_origins_are_rejected(self):
        invalid_origins = (
            "*",
            "http://salamandra-staging.hmimesh.com",
            "https://user:password@salamandra-staging.hmimesh.com",
            "https://salamandra-staging.hmimesh.com/app",
        )
        for origin in invalid_origins:
            with self.subTest(origin=origin), self.assertRaises(ConfigurationError):
                WebConfig.from_environment(
                    staging_environment(
                        SALAMANDRA_APP_ORIGIN=origin,
                        SALAMANDRA_ALLOWED_ORIGINS=origin,
                    )
                )

    def test_production_like_modes_require_postgres_and_disable_dev_fallbacks(self):
        cases = (
            {"SALAMANDRA_DATABASE_URL": ""},
            {"SALAMANDRA_DATABASE_URL": "sqlite:///unsafe.db"},
            {"SALAMANDRA_ALLOW_JSON_DEV": "1"},
            {"SALAMANDRA_ENABLE_DEMO": "1"},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides), self.assertRaises(ConfigurationError):
                WebConfig.from_environment(staging_environment(**overrides))

    def test_proxy_headers_require_explicit_trusted_proxy_addresses(self):
        with self.assertRaises(ConfigurationError):
            WebConfig.from_environment(
                staging_environment(SALAMANDRA_TRUST_PROXY_HEADERS="1")
            )

    def test_forwarded_client_ip_is_used_only_for_an_explicitly_trusted_proxy(self):
        untrusted_request = SimpleNamespace(
            web_config=WebConfig.from_environment(staging_environment()),
            client_address=("127.0.0.1", 12345),
            headers={"X-Forwarded-For": "198.51.100.24"},
        )
        trusted_request = SimpleNamespace(
            web_config=WebConfig.from_environment(
                staging_environment(
                    SALAMANDRA_TRUST_PROXY_HEADERS="1",
                    SALAMANDRA_TRUSTED_PROXY_IPS="127.0.0.1",
                )
            ),
            client_address=("127.0.0.1", 12345),
            headers={"X-Forwarded-For": "198.51.100.24"},
        )

        self.assertEqual(SalamandraServer.client_ip(untrusted_request), "127.0.0.1")
        self.assertEqual(SalamandraServer.client_ip(trusted_request), "198.51.100.24")


class TestWebStagingHttp(unittest.TestCase):
    def setUp(self):
        self.app = ServerHarness()
        self.app.handler.web_config = WebConfig.from_environment(staging_environment())
        self.app.handler.readiness_probe = FakeReadiness(ReadinessResult(True, "ready"))
        self.headers = {
            "Host": "salamandra-staging.hmimesh.com",
            "Origin": STAGING_ORIGIN,
        }

    def tearDown(self):
        self.app.__exit__(None, None, None)

    def test_health_is_public_and_contains_no_sensitive_state(self):
        status, payload, headers = self.app.request(
            "GET", "/health", extra_headers={"Host": self.headers["Host"]}
        )

        self.assertEqual((status, payload), (200, {"status": "ok"}))
        self.assertEqual(headers["cache-control"], "no-store")
        serialized = json.dumps(payload)
        self.assertNotIn("postgres", serialized.lower())
        self.assertNotIn("organization", serialized.lower())

    def test_ready_reports_healthy_without_internal_details(self):
        status, payload, _ = self.app.request(
            "GET", "/ready", extra_headers={"Host": self.headers["Host"]}
        )

        self.assertEqual((status, payload), (200, {"status": "ready"}))

    def test_ready_reports_database_unavailable_generically(self):
        self.app.handler.readiness_probe = FakeReadiness(
            ReadinessResult(False, "database_unavailable")
        )
        status, payload, _ = self.app.request(
            "GET", "/ready", extra_headers={"Host": self.headers["Host"]}
        )

        self.assertEqual(status, 503)
        self.assertEqual(payload, {"status": "not_ready"})
        self.assertNotIn("database", json.dumps(payload).lower())

    def test_ready_reports_wrong_alembic_revision_generically(self):
        self.app.handler.readiness_probe = FakeReadiness(
            ReadinessResult(False, "schema_mismatch")
        )
        status, payload, _ = self.app.request(
            "GET", "/ready", extra_headers={"Host": self.headers["Host"]}
        )

        self.assertEqual(status, 503)
        self.assertEqual(payload, {"status": "not_ready"})
        self.assertNotIn("revision", json.dumps(payload).lower())

    def test_untrusted_origin_is_blocked(self):
        status, _, _ = self.app.request(
            "POST",
            "/api/auth/signin",
            {"email": "owner-a@example.test", "password": "owner-a-password"},
            extra_headers={
                "Host": self.headers["Host"],
                "Origin": "https://attacker.example",
            },
        )

        self.assertEqual(status, 403)

    def test_trusted_origin_is_accepted_with_hardened_cookie(self):
        status, _, headers = self.app.request(
            "POST",
            "/api/auth/signin",
            {"email": "owner-a@example.test", "password": "owner-a-password"},
            extra_headers=self.headers,
        )

        self.assertEqual(status, 200)
        self.assertEqual(headers["access-control-allow-origin"], STAGING_ORIGIN)
        self.assertNotEqual(headers["access-control-allow-origin"], "*")
        cookie = headers["set-cookie"]
        for attribute in (
            "HttpOnly",
            "SameSite=Lax",
            "Secure",
            "Path=/",
            "Max-Age=3600",
            "Expires=",
        ):
            self.assertIn(attribute, cookie)

    def test_untrusted_host_is_rejected(self):
        status, _, _ = self.app.request(
            "GET", "/health", extra_headers={"Host": "attacker.example"}
        )

        self.assertEqual(status, 400)

    def test_state_changing_request_requires_an_origin(self):
        status, _, _ = self.app.request(
            "POST",
            "/api/auth/signin",
            {"email": "owner-a@example.test", "password": "owner-a-password"},
            extra_headers={"Host": self.headers["Host"]},
        )

        self.assertEqual(status, 403)

    def test_public_registration_and_demo_signin_are_unavailable(self):
        register_status, _, _ = self.app.request(
            "POST", "/api/auth/register", {}, extra_headers=self.headers
        )
        demo_status, _, _ = self.app.request(
            "POST", "/api/auth/demo", {}, extra_headers=self.headers
        )

        self.assertEqual((register_status, demo_status), (401, 404))


class TestOperationalLogRedaction(unittest.TestCase):
    def test_sensitive_context_is_not_serialized(self):
        stream = io.StringIO()
        logger = logging.getLogger("salamandra.tests.redaction")
        logger.handlers.clear()
        logger.propagate = False
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonLogFormatter())
        logger.addHandler(handler)

        log_event(
            logger,
            logging.INFO,
            "authentication_failed",
            actor_id="safe-actor",
            result="denied",
            password="must-not-appear",
            token="must-not-appear",
            cookie="must-not-appear",
            database_url="postgresql://user:must-not-appear@db/app",
        )

        output = stream.getvalue()
        self.assertIn("safe-actor", output)
        self.assertNotIn("must-not-appear", output)
        self.assertNotIn("postgresql://", output)


if __name__ == "__main__":
    unittest.main()
