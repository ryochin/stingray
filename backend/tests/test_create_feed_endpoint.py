"""Behavioral tests for POST /api/feeds: RSS flags, the HTML branch, and the first fetch.

_probe_feed is replaced with a canned result and _fetch_single_feed with a
recorder, so no network is touched. The 409 and SSRF 422 cases live in
test_feed_url_unique.py and test_safe_http.py.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

import app as app_module
import repo
from schemas import AppConfig, FeedRow

pytestmark = pytest.mark.usefixtures("clean_db")

URL = "https://a.example.com/feed"


def _post(monkeypatch: pytest.MonkeyPatch, probe: app_module.ProbeResult) -> tuple[Any, list[int]]:
  """POST a feed with the given probe result; return the response and fetched feed ids."""

  async def fake_probe(*_args: Any, **_kwargs: Any) -> app_module.ProbeResult:
    return probe

  fetched: list[int] = []

  # Record from the coroutine body, so the test also fails when the fetch is
  # created but never scheduled.
  async def record_fetch(feed: FeedRow, _config: AppConfig) -> None:
    fetched.append(feed.id)

  monkeypatch.setattr(app_module, "_probe_feed", fake_probe)
  monkeypatch.setattr(app_module, "_fetch_single_feed", record_fetch)
  app_module.app.state.config = AppConfig()
  resp = TestClient(app_module.app).post("/api/feeds", json={"name": "", "url": URL})
  return resp, fetched


def test_foreign_rss_enables_translate_and_summarize_and_fetches(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  probe = app_module.ProbeResult(title="Foreign", translate=True, site_url="https://a.example.com/")
  resp, fetched = _post(monkeypatch, probe)
  assert resp.status_code == 201
  body = resp.json()
  assert (body["name"], body["translate"], body["summarize"]) == ("Foreign", True, True)
  assert body["site_url"] == "https://a.example.com/"
  assert body["extraction_rules"] is None
  assert fetched == [body["id"]]


def test_native_rss_keeps_summarize_off(monkeypatch: pytest.MonkeyPatch) -> None:
  resp, fetched = _post(monkeypatch, app_module.ProbeResult(title="Native"))
  assert resp.status_code == 201
  body = resp.json()
  assert (body["translate"], body["summarize"]) == (False, False)
  assert fetched == [body["id"]]


def test_html_with_feed_candidates_returns_them_without_creating(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  candidates = [{"url": "https://a.example.com/rss", "title": "RSS"}]
  probe = app_module.ProbeResult(title="Page", is_web_page=True, feed_candidates=candidates)
  resp, fetched = _post(monkeypatch, probe)
  assert resp.status_code == 422
  detail = resp.json()["detail"]
  assert (detail["candidates"], detail["page_title"]) == (candidates, "Page")
  assert repo.list_feeds() == []
  assert fetched == []


def test_html_without_candidates_creates_a_web_feed_without_fetching(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  resp, fetched = _post(monkeypatch, app_module.ProbeResult(title="Page", is_web_page=True))
  assert resp.status_code == 201
  assert resp.json()["extraction_rules"] == "{}"
  # Empty rules would only be skipped; the first fetch waits for saved rules.
  assert fetched == []
