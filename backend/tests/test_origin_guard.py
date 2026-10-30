"""Tests for the cross-origin (CSRF) guard.

The decision table is tested on the pure `is_allowed`; the wiring is tested on
the real app without its lifespan, so no DB is touched: rejected requests never
reach a handler, and the one that passes has its persistence stubbed.
"""

from __future__ import annotations

from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient

import app as app_module
import repo
from origin_guard import is_allowed

HOST = "stingray.example:20080"


@pytest.mark.parametrize(
  ("method", "sec_fetch_site", "origin", "host", "allowed"),
  [
    # Safe methods pass whatever the headers say.
    ("GET", "cross-site", "https://evil.example", HOST, True),
    ("HEAD", "cross-site", "https://evil.example", HOST, True),
    ("OPTIONS", "cross-site", "https://evil.example", HOST, True),
    # Only GET/HEAD/OPTIONS are exempt.
    ("TRACE", "cross-site", "", HOST, False),
    # Sec-Fetch-Site decides when present.
    ("POST", "same-origin", "", HOST, True),
    ("POST", "none", "", HOST, True),
    ("POST", "cross-site", "", HOST, False),
    ("POST", "same-site", "", HOST, False),
    ("POST", "bogus", "", HOST, False),
    ("DELETE", "cross-site", "", HOST, False),
    # It takes precedence over Origin either way.
    ("POST", "same-origin", "https://evil.example", HOST, True),
    ("POST", "cross-site", f"http://{HOST}", HOST, False),
    # An empty Sec-Fetch-Site counts as missing.
    ("POST", "", f"http://{HOST}", HOST, True),
    ("POST", "", "https://evil.example", HOST, False),
    # Neither header: not a browser.
    ("POST", "", "", HOST, True),
    # Otherwise Origin's host[:port] must equal Host exactly.
    ("POST", "", f"http://{HOST}", HOST, True),
    ("POST", "", f"https://{HOST}", HOST, True),  # scheme is not compared
    ("POST", "", "http://stingray.example:5173", HOST, False),
    ("POST", "", "http://stingray.example", HOST, False),
    ("POST", "", "http://stingray.example:80", "stingray.example", False),
    ("POST", "", "http://stingray.example", "stingray.example", True),
    ("POST", "", "http://[::1]:20080", "[::1]:20080", True),
    ("POST", "", "http://[::1]:20080", "[::2]:20080", False),
    ("POST", "", "null", HOST, False),
    ("POST", "", "http://[::1", "[::1", False),  # unparsable
    ("POST", "", "null", "", False),
  ],
)
def test_is_allowed(
  method: str, sec_fetch_site: str, origin: str, host: str, allowed: bool
) -> None:
  assert is_allowed(method, sec_fetch_site, origin, host) is allowed


@pytest.fixture
def client() -> TestClient:
  # No `with`: the lifespan (config load, DB) does not run. Host is "testserver".
  return TestClient(app_module.app)


@pytest.fixture
def persisted_filters(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
  calls: list[Any] = []

  def fake_persist(items: list[tuple[str, str]]) -> tuple[int, int]:
    calls.append(items)
    return 0, 0

  monkeypatch.setattr(repo, "import_filters", fake_persist)
  return calls


def _post_filters(client: TestClient, headers: dict[str, str]) -> httpx2.Response:
  return client.post(
    "/api/filters/import",
    files={"file": ("filters.json", b"[]", "application/json")},
    headers=headers,
  )


def test_cross_site_post_is_rejected_before_the_handler(
  client: TestClient, persisted_filters: list[Any]
) -> None:
  resp = _post_filters(client, {"Sec-Fetch-Site": "cross-site"})
  assert resp.status_code == 403
  assert resp.json() == {"detail": "Cross-origin request blocked"}
  assert persisted_filters == []


@pytest.mark.parametrize(
  "headers",
  [
    {},
    {"Sec-Fetch-Site": "same-origin"},
    {"Origin": "http://testserver"},
  ],
)
def test_same_origin_post_reaches_the_handler(
  client: TestClient, persisted_filters: list[Any], headers: dict[str, str]
) -> None:
  resp = _post_filters(client, headers)
  assert resp.status_code == 200
  assert persisted_filters == [[]]


@pytest.mark.parametrize("origin", ["http://testserver:5173", "null", "https://evil.example"])
def test_foreign_origin_post_is_rejected(
  client: TestClient, persisted_filters: list[Any], origin: str
) -> None:
  resp = _post_filters(client, {"Origin": origin})
  assert resp.status_code == 403
  assert persisted_filters == []


def test_first_of_repeated_origin_headers_decides(
  client: TestClient, persisted_filters: list[Any]
) -> None:
  resp = client.post(
    "/api/filters/import",
    files={"file": ("filters.json", b"[]", "application/json")},
    headers=[("Origin", "https://evil.example"), ("Origin", "http://testserver")],
  )
  assert resp.status_code == 403
  assert persisted_filters == []


def test_cross_site_opml_import_is_rejected(
  client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
  calls: list[Any] = []

  async def fake_persist(*args: Any) -> tuple[int, int, int]:
    calls.append(args)
    return 0, 0, 0

  monkeypatch.setattr(app_module, "_persist_opml_import", fake_persist)
  resp = client.post(
    "/api/opml/import",
    files={"file": ("feeds.opml", b"<opml/>", "text/xml")},
    headers={"Origin": "https://evil.example"},
  )
  assert resp.status_code == 403
  assert calls == []


@pytest.mark.parametrize("method", ["HEAD", "OPTIONS"])
def test_safe_methods_get_the_downstream_response(client: TestClient, method: str) -> None:
  # /api/health only answers GET, so passing through means 405, not 403.
  resp = client.request(method, "/api/health", headers={"Sec-Fetch-Site": "cross-site"})
  assert resp.status_code == 405
