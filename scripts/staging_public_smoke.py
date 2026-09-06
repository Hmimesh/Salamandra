from __future__ import annotations

import argparse
import json
import sys
from http.client import HTTPResponse
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen


DEFAULT_BASE_URL = "https://salamandra-staging.hmimesh.com"
REDIRECT_STATUSES = {301, 302, 307, 308}
REQUIRED_HEADERS = (
    "cache-control",
    "content-security-policy",
    "permissions-policy",
    "referrer-policy",
    "strict-transport-security",
    "x-content-type-options",
    "x-frame-options",
    "x-request-id",
)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def normalized_base_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.path not in {"", "/"}:
        raise ValueError("The staging base URL must be an HTTPS origin without a path.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("The staging base URL must not contain credentials or parameters.")
    return value.strip().rstrip("/")


def read_json(response: HTTPResponse) -> dict:
    content_type = response.headers.get("Content-Type", "")
    if "application/json" not in content_type:
        raise AssertionError(f"Expected JSON but received {content_type or 'no content type'}.")
    payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError("Expected a JSON object response.")
    return payload


def request(base_url: str, path: str, timeout: float, headers: dict | None = None):
    return urlopen(
        Request(f"{base_url}{path}", headers=headers or {}),
        timeout=timeout,
    )


def verify_http_redirect(base_url: str, timeout: float) -> None:
    parsed = urlsplit(base_url)
    http_url = f"http://{parsed.netloc}/"
    opener = build_opener(NoRedirect())
    try:
        response = opener.open(Request(http_url), timeout=timeout)
        status = response.status
        location = response.headers.get("Location", "")
        response.close()
    except HTTPError as error:
        status = error.code
        location = error.headers.get("Location", "")
    redirect = urlsplit(location)
    expected = urlsplit(base_url)
    if (
        status not in REDIRECT_STATUSES
        or redirect.scheme != "https"
        or redirect.hostname != expected.hostname
        or redirect.port != expected.port
    ):
        raise AssertionError(
            f"HTTP did not redirect to the canonical HTTPS origin (status {status})."
        )


def verify_public_endpoints(base_url: str, timeout: float) -> None:
    with request(base_url, "/health", timeout) as response:
        if response.status != 200 or read_json(response) != {"status": "ok"}:
            raise AssertionError("/health did not return the expected liveness response.")
        response_headers = {name.lower(): value for name, value in response.headers.items()}
    missing = [name for name in REQUIRED_HEADERS if name not in response_headers]
    if missing:
        raise AssertionError("Missing security headers: " + ", ".join(missing))

    with request(base_url, "/ready", timeout) as response:
        if response.status != 200 or read_json(response) != {"status": "ready"}:
            raise AssertionError("/ready did not report a ready PostgreSQL runtime.")

    with request(base_url, "/api/state", timeout) as response:
        state = read_json(response)
    if state.get("auth", {}).get("authenticated") is not False:
        raise AssertionError("Signed-out state did not report an unauthenticated user.")
    if state.get("events", {}).get("events"):
        raise AssertionError("Signed-out state exposed tenant events.")
    inventories = state.get("inventories", {})
    if any(inventory.get("items") for inventory in inventories.values() if isinstance(inventory, dict)):
        raise AssertionError("Signed-out state exposed tenant inventory.")


def verify_forged_host_rejected(base_url: str, timeout: float) -> None:
    try:
        with request(base_url, "/health", timeout, {"Host": "untrusted.example"}) as response:
            status = response.status
    except HTTPError as error:
        status = error.code
    # Railway can reject an unknown Host at its routing edge before Salamandra runs.
    if status not in {400, 403, 404, 421}:
        raise AssertionError(f"Forged Host was not rejected (got HTTP {status}).")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run non-destructive public Salamandra staging smoke checks."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    try:
        base_url = normalized_base_url(args.base_url)
        verify_http_redirect(base_url, args.timeout)
        verify_public_endpoints(base_url, args.timeout)
        verify_forged_host_rejected(base_url, args.timeout)
    except (
        AssertionError,
        HTTPError,
        URLError,
        TimeoutError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1

    print(f"PASS: public staging smoke checks succeeded for {base_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
