"""Tests for app._fetch_single_feed, the manual / just-created single-feed fetch.

It shares feeds.fetch_one_feed and fetcher.classify_outcome with the scheduled
path, so a feed's health comes out the same whichever path fetched it. The
network goes through MockTransport, the feed cache lives in a dict, and the
repo writes are recorded instead of hitting the DB.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import httpx
import pytest

import app
import repo
from fetcher import STALE_CACHE_DIAGNOSTICS
from models import Article
from safe_http import SafeClient
from schemas import AppConfig, FeedRow
from tests.fetch_stubs import resolve_public, rss, stub_network

FEED_URL: str = "https://f.example.com/feed.xml"
WEB_URL: str = "https://web.example.com/news"
RULES: str = json.dumps({"item": "li", "title": "a", "link": "a"})
PAGE: str = '<ul><li><a href="/1">One</a></li><li><a href="/2">Two</a></li></ul>'
NOW: datetime = datetime.now(tz=UTC)

Handler = Callable[[httpx.Request], httpx.Response]


@dataclass
class Recorder:
  requested: list[str] = field(default_factory=lambda: [])
  upserted: list[list[Article]] = field(default_factory=lambda: [])
  site_urls: list[str] = field(default_factory=lambda: [])
  statuses: list[tuple[str, str | None]] = field(default_factory=lambda: [])
  inserted: int | None = None
  upsert_error: Exception | None = None


@pytest.fixture
def rec(monkeypatch: pytest.MonkeyPatch) -> Recorder:
  recorder = Recorder()

  def upsert(articles: list[Article], _feed_id_map: dict[str, int]) -> int:
    if recorder.upsert_error is not None:
      raise recorder.upsert_error
    recorder.upserted.append(articles)
    return len(articles) if recorder.inserted is None else recorder.inserted

  def update_site_url(_feed_id: int, site_url: str) -> None:
    recorder.site_urls.append(site_url)

  def update_status(_feed_id: int, *, health: str, error: str | None = None) -> None:
    recorder.statuses.append((health, error))

  monkeypatch.setattr(repo, "upsert_articles", upsert)
  monkeypatch.setattr(repo, "update_feed_site_url", update_site_url)
  monkeypatch.setattr(repo, "update_feed_fetch_status", update_status)
  return recorder


def _run(
  monkeypatch: pytest.MonkeyPatch,
  rec: Recorder,
  feed: FeedRow,
  handler: Handler,
  *,
  cache: dict[str, dict[str, object]] | None = None,
  config: AppConfig | None = None,
) -> None:
  def recording(request: httpx.Request) -> httpx.Response:
    rec.requested.append(str(request.url))
    return handler(request)

  stub_network(monkeypatch, recording, cache)
  # _fetch_single_feed and _probe_feed build their own client in app.
  monkeypatch.setattr(
    app,
    "SafeClient",
    partial(SafeClient, transport=httpx.MockTransport(recording), resolver=resolve_public),
  )
  asyncio.run(app._fetch_single_feed(feed, config or AppConfig()))


def _rss_feed(**kwargs: Any) -> FeedRow:
  return FeedRow(id=1, name="F", url=FEED_URL, site_url="https://f.example.com/", **kwargs)


def _web_feed(rules: str, **kwargs: Any) -> FeedRow:
  return FeedRow(id=2, name="W", url=WEB_URL, extraction_rules=rules, **kwargs)


def _ok(body: str) -> Handler:
  return lambda _request: httpx.Response(200, text=body)


def _cached(body: str) -> dict[str, dict[str, object]]:
  # Shaped like stub_network's own save, so the content-hash check matches.
  return {
    FEED_URL: {
      "etag": None,
      "last_modified": None,
      "body": body,
      "content_hash": hashlib.sha256(body.encode()).hexdigest(),
    },
  }


class TestRssHealth:
  def test_fresh_fetch_is_ok(self, monkeypatch: pytest.MonkeyPatch, rec: Recorder):
    _run(monkeypatch, rec, _rss_feed(), _ok(rss(("https://f.example.com/1", NOW))))
    assert [len(a) for a in rec.upserted] == [1]
    assert rec.statuses == [("ok", None)]

  def test_nothing_new_is_still_ok(self, monkeypatch: pytest.MonkeyPatch, rec: Recorder):
    rec.inserted = 0
    _run(monkeypatch, rec, _rss_feed(), _ok(rss(("https://f.example.com/1", NOW))))
    assert rec.statuses == [("ok", None)]

  def test_empty_feed_is_ok_without_upsert(self, monkeypatch: pytest.MonkeyPatch, rec: Recorder):
    _run(monkeypatch, rec, _rss_feed(), _ok(rss()))
    assert rec.upserted == []
    assert rec.statuses == [("ok", None)]

  def test_unchanged_body_is_ok(self, monkeypatch: pytest.MonkeyPatch, rec: Recorder):
    body = rss(("https://f.example.com/1", NOW))
    _run(monkeypatch, rec, _rss_feed(), _ok(body), cache=_cached(body))
    assert rec.statuses == [("ok", None)]

  def test_stale_cache_on_5xx_is_degraded(self, monkeypatch: pytest.MonkeyPatch, rec: Recorder):
    body = rss(("https://f.example.com/1", NOW))
    _run(
      monkeypatch,
      rec,
      _rss_feed(),
      lambda _request: httpx.Response(503),
      cache=_cached(body),
    )
    assert rec.statuses == [("degraded", STALE_CACHE_DIAGNOSTICS["5xx-cache"])]

  def test_304_without_cache_is_degraded(self, monkeypatch: pytest.MonkeyPatch, rec: Recorder):
    _run(monkeypatch, rec, _rss_feed(), lambda _request: httpx.Response(304))
    assert rec.upserted == []
    assert rec.statuses == [("degraded", STALE_CACHE_DIAGNOSTICS["304-empty"])]


class TestWebFeeds:
  @pytest.mark.parametrize("rules", ["{}", json.dumps({"title": "a"})], ids=["empty", "no-item"])
  def test_unusable_rules_are_degraded_without_any_request(
    self, monkeypatch: pytest.MonkeyPatch, rec: Recorder, rules: str
  ):
    # "no-item" used to reach parse_web_page and fail with KeyError('item').
    _run(monkeypatch, rec, _web_feed(rules), _ok(PAGE))
    assert rec.requested == []
    assert rec.upserted == []
    assert rec.site_urls == []
    assert rec.statuses == [("degraded", "extraction rules not configured")]

  def test_missing_site_url_is_filled_with_the_page_url(
    self, monkeypatch: pytest.MonkeyPatch, rec: Recorder
  ):
    # The probe re-fetches the page, sees HTML and takes the page URL itself.
    page = f"<html><body>{PAGE}</body></html>"
    _run(monkeypatch, rec, _web_feed(RULES), _ok(page))
    assert rec.requested == [WEB_URL, WEB_URL]
    assert [len(a) for a in rec.upserted] == [2]
    assert rec.site_urls == [WEB_URL]
    assert rec.statuses == [("ok", None)]


class TestRssPersistence:
  def test_max_items_per_feed_applies(self, monkeypatch: pytest.MonkeyPatch, rec: Recorder):
    items = [(f"https://f.example.com/{i}", NOW) for i in range(5)]
    _run(
      monkeypatch,
      rec,
      _rss_feed(),
      _ok(rss(*items)),
      config=AppConfig(max_items_per_feed=3),
    )
    assert [len(a) for a in rec.upserted] == [3]

  def test_no_age_cutoff(self, monkeypatch: pytest.MonkeyPatch, rec: Recorder):
    # Documented in the README: a single-feed refresh keeps old articles.
    old = NOW - timedelta(days=30)
    _run(
      monkeypatch,
      rec,
      _rss_feed(),
      _ok(rss(("https://f.example.com/old", old))),
      config=AppConfig(max_age_hours=1),
    )
    assert [a.url for a in rec.upserted[0]] == ["https://f.example.com/old"]

  def test_missing_site_url_is_backfilled(self, monkeypatch: pytest.MonkeyPatch, rec: Recorder):
    _run(
      monkeypatch,
      rec,
      _rss_feed().model_copy(update={"site_url": None}),
      _ok(rss(("https://f.example.com/1", NOW))),
    )
    assert rec.site_urls == ["https://example.com/"]


class TestFailures:
  def test_fetch_error_is_failing(self, monkeypatch: pytest.MonkeyPatch, rec: Recorder):
    _run(monkeypatch, rec, _rss_feed(), lambda _request: httpx.Response(404))
    assert len(rec.statuses) == 1
    health, error = rec.statuses[0]
    assert health == "failing"
    assert error is not None and "404" in error

  def test_save_error_is_failing(self, monkeypatch: pytest.MonkeyPatch, rec: Recorder):
    rec.upsert_error = RuntimeError("db down")
    _run(monkeypatch, rec, _rss_feed(), _ok(rss(("https://f.example.com/1", NOW))))
    assert rec.statuses == [("failing", "db down")]
