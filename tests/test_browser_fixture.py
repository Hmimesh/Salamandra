from __future__ import annotations

import http.client
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path


class TestBrowserFixtureLifecycle(unittest.TestCase):
    def test_parent_pipe_eof_stops_fixture_and_releases_port(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        process = subprocess.Popen(
            [sys.executable, "tests/e2e_server.py", "--port", str(port), "--parent-stdin"],
            cwd=Path(__file__).resolve().parents[1],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 15
            healthy = False
            while time.monotonic() < deadline and process.poll() is None:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                try:
                    connection.request("GET", "/health")
                    response = connection.getresponse()
                    healthy = response.status == 200
                    response.read()
                    if healthy:
                        break
                except OSError:
                    time.sleep(0.05)
                finally:
                    connection.close()
            self.assertTrue(healthy, "Fixture did not become healthy")
            # communicate closes the parent pipe, including on a failed-test teardown.
            _, errors = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, errors.decode())
            with socket.socket() as probe:
                self.assertNotEqual(probe.connect_ex(("127.0.0.1", port)), 0)
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)
