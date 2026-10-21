"""`feeds.fetch_all`: per-feed callback, failure isolation, and the
concurrency cap. The network is a MockTransport (see fetch_stubs).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

import feeds
from models import Article
from safe_http import FetchSettings
from schemas import AppConfig, FeedRow
from tests.fetch_stubs import by_host, rss, stub_network

NOW = datetime.now(tz=UTC)
SETTINGS = FetchSettings.from_config(AppConfig())

Call = tuple[str, list[str], str | None, str, Exception | None]


def _feed(name: str) -> dict[str, Any]:
  return {"name": name, "url": f"https://{name}.example.com/feed"}


def _recorder(calls: list[Call]) -> feeds.OnFeedDone:
  async def on_feed_done(
    feed: dict[str, Any],
    articles: list[Article],
    source_tag: str | None,
    kind: str,
    fetch_exc: Exception | None,
  ) -> None:
    calls.append((feed["name"], [a.url for a in articles], source_tag, kind, fetch_exc))

  return on_feed_done


def test_callback_runs_once_per_feed_whatever_the_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
  stub_network(
    monkeypatch,
    by_host(
      {
        "ok": httpx.Response(200, text=rss(("https://ok.example.com/1", NOW))),
        "empty": httpx.Response(200, text=rss()),
        "gone": httpx.Response(404),
      }
    ),
  )
  calls: list[Call] = []

  articles = asyncio.run(
    feeds.fetch_all(
      [_feed("ok"), _feed("empty"), _feed("gone")],
      on_feed_done=_recorder(calls),
      fetch_settings=SETTINGS,
    )
  )

  by_name = {c[0]: c for c in calls}
  assert len(calls) == 3
  assert by_name["ok"][1:4] == (["https://ok.example.com/1"], "fresh", "rss")
  assert by_name["ok"][4] is None
  assert by_name["empty"][1] == []
  assert by_name["empty"][4] is None
  assert by_name["gone"][1] == []
  assert isinstance(by_name["gone"][4], httpx.HTTPStatusError)
  assert [a.url for a in articles] == ["https://ok.example.com/1"]


def test_callback_error_does_not_stop_other_feeds(monkeypatch: pytest.MonkeyPatch) -> None:
  stub_network(
    monkeypatch,
    lambda req: httpx.Response(200, text=rss((f"https://{req.url.host}/1", NOW))),
  )
  seen: list[str] = []

  async def on_feed_done(feed: dict[str, Any], *_args: Any) -> None:
    seen.append(feed["name"])
    if feed["name"] == "bad":
      raise RuntimeError("persist failed")

  articles = asyncio.run(
    feeds.fetch_all(
      [_feed("bad"), _feed("good")],
      on_feed_done=on_feed_done,
      fetch_settings=SETTINGS,
    )
  )

  assert sorted(seen) == ["bad", "good"]
  assert sorted(a.url for a in articles) == [
    "https://bad.example.com/1",
    "https://good.example.com/1",
  ]


def test_callback_receives_only_articles_inside_the_window(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  body = rss(
    ("https://f.example.com/new", NOW - timedelta(hours=1)),
    ("https://f.example.com/old", NOW - timedelta(hours=72)),
  )
  stub_network(monkeypatch, lambda _req: httpx.Response(200, text=body))
  calls: list[Call] = []

  asyncio.run(
    feeds.fetch_all(
      [_feed("f")],
      max_age_hours=48,
      on_feed_done=_recorder(calls),
      fetch_settings=SETTINGS,
    )
  )

  assert calls[0][1] == ["https://f.example.com/new"]


def test_at_most_five_feeds_are_fetched_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
  async def scenario() -> tuple[int, int]:
    release = asyncio.Event()
    in_flight = 0
    peak = 0
    reached_cap = asyncio.Event()

    async def handler(_request: httpx.Request) -> httpx.Response:
      nonlocal in_flight, peak
      in_flight += 1
      peak = max(peak, in_flight)
      if in_flight >= feeds._CONCURRENCY:
        reached_cap.set()
      await release.wait()
      in_flight -= 1
      return httpx.Response(200, text=rss())

    stub_network(monkeypatch, handler)
    task = asyncio.create_task(
      feeds.fetch_all([_feed(f"f{i}") for i in range(8)], fetch_settings=SETTINGS)
    )
    await asyncio.wait_for(reached_cap.wait(), timeout=5)
    # Give any feed past the cap time to sneak in before measuring; it has
    # to get through the cache read (a worker thread) to reach the handler.
    await asyncio.sleep(0.2)
    blocked_peak = peak
    release.set()
    await asyncio.wait_for(task, timeout=5)
    return blocked_peak, peak

  blocked_peak, final_peak = asyncio.run(scenario())

  assert blocked_peak == 5
  assert final_peak == 5


# -- web feeds (extraction rules) --

RULES: dict[str, str] = {"item": "li", "title": "a", "link": "a"}
PAGE: str = '<ul><li><a href="/1">One</a></li><li><a href="/2">Two</a></li></ul>'


def _web_feed(rules: object) -> dict[str, Any]:
  return {"name": "web", "url": "https://web.example.com/news", "extraction_rules": rules}


def _fetch_web(
  monkeypatch: pytest.MonkeyPatch,
  rules: object,
  response: httpx.Response | None = None,
) -> tuple[Call, list[str], dict[str, dict[str, object]]]:
  """Fetch one web feed; return its callback call, the requested URLs, and the feed cache."""
  requested: list[str] = []
  cache: dict[str, dict[str, object]] = {}

  def handler(request: httpx.Request) -> httpx.Response:
    requested.append(str(request.url))
    return response if response is not None else httpx.Response(200, text=PAGE)

  stub_network(monkeypatch, handler, cache)
  calls: list[Call] = []
  asyncio.run(
    feeds.fetch_all([_web_feed(rules)], on_feed_done=_recorder(calls), fetch_settings=SETTINGS)
  )
  assert len(calls) == 1
  return calls[0], requested, cache


@pytest.mark.parametrize("rules", [json.dumps(RULES), RULES], ids=["json", "dict"])
def test_web_feed_scrapes_the_page_with_its_rules(
  monkeypatch: pytest.MonkeyPatch,
  rules: object,
) -> None:
  call, requested, cache = _fetch_web(monkeypatch, rules)
  assert call[1:] == (
    ["https://web.example.com/1", "https://web.example.com/2"],
    None,
    "web",
    None,
  )
  assert requested == ["https://web.example.com/news"]
  assert cache == {}  # web pages bypass the feed cache


@pytest.mark.parametrize(
  ("rules", "warning"),
  [
    ("{}", "no extraction rules configured"),
    (json.dumps({"title": "a", "link": "a"}), "extraction rules incomplete"),
  ],
  ids=["unconfigured", "no_item"],
)
def test_web_feed_without_usable_rules_is_skipped(
  monkeypatch: pytest.MonkeyPatch,
  capsys: pytest.CaptureFixture[str],
  rules: str,
  warning: str,
) -> None:
  call, requested, _cache = _fetch_web(monkeypatch, rules)
  assert call[1:] == ([], None, "web-norules", None)
  assert requested == []
  assert f"[web/skip] web: {warning}" in capsys.readouterr().out


def test_broken_rules_json_is_reported_as_an_rss_failure(monkeypatch: pytest.MonkeyPatch) -> None:
  # Pins current behavior: the exception path labels every failure "rss".
  call, requested, _cache = _fetch_web(monkeypatch, "{")
  assert call[1:4] == ([], None, "rss")
  assert isinstance(call[4], json.JSONDecodeError)
  assert requested == []


def test_web_page_http_error_is_reported_as_an_rss_failure(monkeypatch: pytest.MonkeyPatch) -> None:
  call, requested, _cache = _fetch_web(monkeypatch, json.dumps(RULES), httpx.Response(404))
  assert call[1:4] == ([], None, "rss")
  assert isinstance(call[4], httpx.HTTPStatusError)
  assert requested == ["https://web.example.com/news"]


@pytest.mark.parametrize(
  ("rules", "has_key"),
  [(None, False), ("", False), ("{}", True)],
)
def test_feed_cfg_carries_rules_only_when_non_empty(rules: str | None, has_key: bool) -> None:
  # "" drops out here, so such a feed is fetched as RSS (export treats it as
  # web; normal write paths never store "").
  cfg = FeedRow(name="F", url="https://f.example.com/", extraction_rules=rules).to_feed_cfg()
  assert ("extraction_rules" in cfg) is has_key
