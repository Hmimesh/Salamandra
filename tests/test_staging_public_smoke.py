from __future__ import annotations

import importlib.util
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "staging_public_smoke", ROOT / "scripts" / "staging_public_smoke.py"
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load the staging smoke script.")
SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)


class TestStagingPublicSmoke(unittest.TestCase):
    def test_railway_edge_404_is_accepted_only_by_forged_host_probe(self):
        @contextmanager
        def rejected(*_args, **_kwargs):
            raise HTTPError("https://staging.test/health", 404, "not found", {}, None)
            yield

        with patch.object(SMOKE, "request", rejected):
            SMOKE.verify_forged_host_rejected("https://staging.test", 1)
            with self.assertRaises(HTTPError):
                SMOKE.verify_public_endpoints("https://staging.test", 1)


if __name__ == "__main__":
    unittest.main()
