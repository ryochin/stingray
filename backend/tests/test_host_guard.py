"""Tests for the DNS rebinding (Host allowlist) guard and its config.

The decision table is tested on the pure `is_allowed_host`; the wiring is
tested on the real app without its lifespan, so no DB is touched: a request
that passes is sent to a path no API route serves, which the SPA fallback
answers without a 403.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app as app_module
import host_guard
from host_guard import is_allowed_host
from schemas import AppConfig, WebConfig

ALLOWED: frozenset[str] = frozenset({"stingray.example"})
UNROUTED_PATH = "/api/__no_such_route__"


@pytest.mark.parametrize(
  ("host", "allowed"),
  [
    # Not a browser.
    ("", True),
    # IP literals, with and without a port.
    ("192.168.1.10", True),
    ("192.168.1.10:20080", True),
    ("127.0.0.1:20080", True),
    ("[::1]:20080", True),
    ("[::1]", True),
    ("[fe80::1", False),  # unterminated bracket
    # localhost and its subdomains, in any case and with a trailing dot.
    ("localhost:20080", True),
    ("LocalHost", True),
    ("localhost.:5173", True),
    ("app.localhost:20080", True),
    # Configured names match exactly, ignoring case, port and a trailing dot.
    ("stingray.example", True),
    ("Stingray.Example.:20080", True),
    ("sub.stingray.example", False),
    # Rebinding names.
    ("evil.example:20080", False),
    ("localhost.evil.example", False),
    ("127.0.0.1.nip.io:20080", False),
    ("2130706433", False),  # browsers send dotted quads only
  ],
)
def test_is_allowed_host(host: str, allowed: bool) -> None:
  assert is_allowed_host(host, ALLOWED) is allowed


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
  # Only the built-in rules, without conftest's "testserver" exemption.
  monkeypatch.setattr(host_guard, "EXTRA_ALLOWED_HOSTS", frozenset[str]())
  # No `with`: the lifespan (config load, DB) does not run.
  return TestClient(app_module.app)


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_unknown_host_is_refused_for_every_method(client: TestClient, method: str) -> None:
  resp = client.request(method, "/api/articles", headers={"Host": "evil.example:20080"})
  assert resp.status_code == 403
  assert resp.json() == {"detail": "Host not allowed"}


def test_static_files_are_guarded_too(client: TestClient) -> None:
  resp = client.get("/", headers={"Host": "evil.example:20080"})
  assert resp.status_code == 403


@pytest.mark.parametrize("host", ["localhost:20080", "192.168.1.10:20080"])
def test_builtin_hosts_pass(client: TestClient, host: str) -> None:
  resp = client.get(UNROUTED_PATH, headers={"Host": host})
  assert resp.status_code != 403


def test_configured_host_passes(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
  config = AppConfig(web=WebConfig(allowed_hosts=["Stingray.Example"]))
  monkeypatch.setattr(app_module.app.state, "config", config, raising=False)
  assert client.get(UNROUTED_PATH, headers={"Host": "stingray.example:20080"}).status_code != 403
  assert client.get(UNROUTED_PATH, headers={"Host": "other.example"}).status_code == 403


def test_host_guard_runs_before_origin_guard(client: TestClient) -> None:
  # A cross-site POST under an unknown Host reports the Host, not the Origin.
  resp = client.post(
    "/api/articles/read-all",
    headers={"Host": "evil.example", "Sec-Fetch-Site": "cross-site"},
  )
  assert resp.json() == {"detail": "Host not allowed"}


class TestWebConfig:
  def test_default_is_empty(self):
    assert AppConfig().web.allowed_hosts == []

  def test_entries_are_normalized(self):
    config = WebConfig(allowed_hosts=[" Stingray.Example. ", "ぼく.example"])
    assert config.allowed_hosts == ["stingray.example", "xn--y8jve.example"]

  @pytest.mark.parametrize(
    "entry", ["", "  ", "stingray.example:20080", "http://stingray.example", "a b", "[::1]"]
  )
  def test_non_bare_host_rejected(self, entry: str):
    with pytest.raises(ValidationError):
      WebConfig(allowed_hosts=[entry])

  def test_unknown_key_rejected(self):
    with pytest.raises(ValidationError):
      WebConfig.model_validate({"allowed_host": ["x"]})
