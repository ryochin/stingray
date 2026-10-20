"""fetcher._backfill_site_urls: fill a missing site_url from the feed's <link>.

Best effort: a feed that fails is logged and skipped, never stopping the others
or the refresh. HTTP goes through a MockTransport-backed SafeClient.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import partial
from typing import Any

import httpx
import pytest

import fetcher
import repo
from safe_http import FetchSettings, SafeClient
from schemas import AppConfig, FeedRow
from tests.fetch_stubs import by_host, resolve_public, rss

pytestmark = pytest.mark.usefixtures("clean_db")

WARNING: str = "site_url backfill failed"


def _stub_http(
  monkeypatch: pytest.MonkeyPatch,
  handler: Callable[[httpx.Request], httpx.Response],
) -> list[str]:
  """Route the backfill's HTTP through `handler`; return the requested URLs."""
  requested: list[str] = []

  def recording(request: httpx.Request) -> httpx.Response:
    requested.append(str(request.url))
    return handler(request)

  monkeypatch.setattr(
    fetcher,
    "SafeClient",
    partial(SafeClient, transport=httpx.MockTransport(recording), resolver=resolve_public),
  )
  return requested


def _feed(name: str, site_url: str | None = None) -> FeedRow:
  return repo.add_feed(
    FeedRow(name=name, url=f"https://{name}.example.com/rss", site_url=site_url),
  )


def _backfill() -> None:
  feeds: list[FeedRow] = repo.list_feeds()
  asyncio.run(fetcher._backfill_site_urls(feeds, FetchSettings.from_config(AppConfig())))


def _site_url(name: str) -> str | None:
  return next(f.site_url for f in repo.list_feeds() if f.name == name)


def test_fills_a_missing_site_url_from_the_feed_link(monkeypatch: pytest.MonkeyPatch):
  _stub_http(monkeypatch, by_host({"ok": httpx.Response(200, text=rss())}))
  _feed("ok")
  _backfill()
  assert _site_url("ok") == "https://example.com/"


def test_feeds_that_have_a_site_url_are_not_fetched(monkeypatch: pytest.MonkeyPatch):
  requested = _stub_http(monkeypatch, by_host({"empty": httpx.Response(200, text=rss())}))
  _feed("known", site_url="https://known.example.com/")
  _feed("empty", site_url="")
  _backfill()
  assert requested == ["https://empty.example.com/rss"]
  assert _site_url("known") == "https://known.example.com/"


def test_no_client_is_built_when_nothing_is_missing(monkeypatch: pytest.MonkeyPatch):
  def no_client(*_args: Any, **_kwargs: Any) -> SafeClient:
    raise AssertionError("SafeClient must not be built")

  monkeypatch.setattr(fetcher, "SafeClient", no_client)
  _feed("known", site_url="https://known.example.com/")
  _backfill()


def test_a_failing_feed_is_logged_and_does_not_stop_the_others(
  monkeypatch: pytest.MonkeyPatch,
  capsys: pytest.CaptureFixture[str],
):
  _stub_http(
    monkeypatch,
    by_host({"gone": httpx.Response(404), "ok": httpx.Response(200, text=rss())}),
  )
  _feed("gone")
  _feed("ok")
  _backfill()
  assert _site_url("gone") is None
  assert _site_url("ok") == "https://example.com/"
  out = capsys.readouterr().out
  assert f"{WARNING} for gone" in out
  assert f"{WARNING} for ok" not in out


def test_a_body_without_a_link_is_left_alone_quietly(
  monkeypatch: pytest.MonkeyPatch,
  capsys: pytest.CaptureFixture[str],
):
  _stub_http(monkeypatch, by_host({"page": httpx.Response(200, text="<html><p>hi</p></html>")}))
  _feed("page")
  _backfill()
  assert _site_url("page") is None
  assert WARNING not in capsys.readouterr().out


def test_a_failed_db_write_is_logged_not_raised(
  monkeypatch: pytest.MonkeyPatch,
  capsys: pytest.CaptureFixture[str],
):
  _stub_http(monkeypatch, by_host({"ok": httpx.Response(200, text=rss())}))

  def broken_update(_feed_id: int, _site_url: str | None) -> None:
    raise RuntimeError("db down")

  monkeypatch.setattr(repo, "update_feed_site_url", broken_update)
  _feed("ok")
  _backfill()
  assert f"{WARNING} for ok: db down" in capsys.readouterr().out
