"""_fetch_with_cache: conditional headers and cache fallbacks.

The 304 and network-error fallbacks are covered in test_safe_http.py; these
cover the request headers, the HTTP-error fallback, unusable cache entries,
the content-hash shortcut, what gets saved, and a network error with no cache.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable

import httpx
import pytest

import feeds
from safe_http import FetchSettings, SafeClient
from schemas import AppConfig

_URL = "https://example.com/feed"


async def _resolve(_host: str, _port: int) -> list[str]:
  return ["93.184.216.34"]


def _stub_cache(
  monkeypatch: pytest.MonkeyPatch,
  cached: dict[str, object] | None,
) -> list[tuple[object, ...]]:
  """Serve `cached` from the feed cache and record every save into the returned list."""
  saved: list[tuple[object, ...]] = []

  def load(_url: str) -> dict[str, object] | None:
    return cached

  def save(*args: object) -> None:
    saved.append(args)

  monkeypatch.setattr(feeds, "load_feed_cache", load)
  monkeypatch.setattr(feeds, "save_feed_cache", save)
  return saved


def _fetch(
  monkeypatch: pytest.MonkeyPatch,
  cached: dict[str, object] | None,
  status: int,
  seen: list[httpx.Request] | None = None,
) -> tuple[str, bool, str]:
  _stub_cache(monkeypatch, cached)

  def handler(request: httpx.Request) -> httpx.Response:
    if seen is not None:
      seen.append(request)
    return httpx.Response(status, text="<rss>new</rss>")

  async def run() -> tuple[str, bool, str]:
    settings = FetchSettings.from_config(AppConfig.model_validate({}))
    async with SafeClient(
      settings,
      timeout=5,
      transport=httpx.MockTransport(handler),
      resolver=_resolve,
    ) as client:
      return await feeds._fetch_with_cache(client, _URL)

  return asyncio.run(run())


def test_cached_validators_are_sent_as_conditional_headers(monkeypatch: pytest.MonkeyPatch) -> None:
  seen: list[httpx.Request] = []
  cached: dict[str, object] = {
    "body": "<rss/>",
    "etag": '"e1"',
    "last_modified": "Mon, 01 Jan 2026 00:00:00 GMT",
  }
  _fetch(monkeypatch, cached, 304, seen)
  assert seen[0].headers["If-None-Match"] == '"e1"'
  assert seen[0].headers["If-Modified-Since"] == "Mon, 01 Jan 2026 00:00:00 GMT"


@pytest.mark.parametrize("status", [403, 429, 500, 502, 503, 504])
def test_http_error_falls_back_to_the_cache(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
  assert _fetch(monkeypatch, {"body": "<rss/>"}, status) == ("<rss/>", True, "5xx-cache")


def test_other_http_error_raises_even_with_a_cache(monkeypatch: pytest.MonkeyPatch) -> None:
  with pytest.raises(httpx.HTTPStatusError):
    _fetch(monkeypatch, {"body": "<rss/>"}, 404)


@pytest.mark.parametrize("cached", [None, {"etag": '"e1"'}, {"body": 123, "etag": '"e1"'}])
def test_unusable_cache_is_ignored(
  monkeypatch: pytest.MonkeyPatch, cached: dict[str, object] | None
) -> None:
  seen: list[httpx.Request] = []
  assert _fetch(monkeypatch, cached, 304, seen) == ("", True, "304-empty")
  assert "If-None-Match" not in seen[0].headers
  with pytest.raises(httpx.HTTPStatusError):
    _fetch(monkeypatch, cached, 503)


@pytest.mark.parametrize(("status", "tag"), [(304, "304"), (503, "5xx-cache")])
def test_empty_cached_body_is_still_served(
  monkeypatch: pytest.MonkeyPatch, status: int, tag: str
) -> None:
  assert _fetch(monkeypatch, {"body": "", "etag": '"e1"'}, status) == ("", True, tag)


def test_fresh_body_is_returned(monkeypatch: pytest.MonkeyPatch) -> None:
  assert _fetch(monkeypatch, {"body": "<rss/>"}, 200) == ("<rss>new</rss>", False, "fresh")


def _get(handler: Callable[[httpx.Request], httpx.Response]) -> tuple[str, bool, str]:
  async def run() -> tuple[str, bool, str]:
    settings = FetchSettings.from_config(AppConfig.model_validate({}))
    async with SafeClient(
      settings,
      timeout=5,
      transport=httpx.MockTransport(handler),
      resolver=_resolve,
    ) as client:
      return await feeds._fetch_with_cache(client, _URL)

  return asyncio.run(run())


def test_body_matching_the_cached_hash_is_unchanged_and_not_saved(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  body = "<rss>same</rss>"
  cached: dict[str, object] = {
    "body": body,
    "content_hash": hashlib.sha256(body.encode()).hexdigest(),
  }
  saved = _stub_cache(monkeypatch, cached)

  assert _get(lambda _req: httpx.Response(200, text=body)) == (body, True, "unchanged")
  assert saved == []


def test_fresh_body_is_saved_with_the_response_validators(monkeypatch: pytest.MonkeyPatch) -> None:
  saved = _stub_cache(monkeypatch, None)
  headers = {"ETag": '"e2"', "Last-Modified": "Tue, 02 Jan 2026 00:00:00 GMT"}

  result = _get(lambda _req: httpx.Response(200, text="<rss>new</rss>", headers=headers))

  assert result == ("<rss>new</rss>", False, "fresh")
  assert saved == [(_URL, '"e2"', "Tue, 02 Jan 2026 00:00:00 GMT", "<rss>new</rss>")]


def test_network_error_without_a_cache_raises(monkeypatch: pytest.MonkeyPatch) -> None:
  def no_cache(_url: str) -> None:
    return None

  monkeypatch.setattr(feeds, "load_feed_cache", no_cache)

  def handler(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused", request=request)

  with pytest.raises(httpx.ConnectError):
    _get(handler)
