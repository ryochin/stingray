"""Shared stubs for tests that drive `feeds.fetch_all` end to end.

`fetch_all` builds its own SafeClient, sleeps a random delay per feed, and
reads/writes the on-disk feed cache, so tests swap all three out.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Coroutine
from datetime import datetime
from email.utils import format_datetime
from functools import partial
from types import SimpleNamespace

import httpx
import pytest

import feeds
from safe_http import SafeClient

Handler = (
  Callable[[httpx.Request], httpx.Response]
  | Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]
)


async def resolve_public(_host: str, _port: int) -> list[str]:
  """A resolver that maps every host to a public IP, so SafeClient lets it through."""
  return ["93.184.216.34"]


def stub_network(
  monkeypatch: pytest.MonkeyPatch,
  handler: Handler,
  cache: dict[str, dict[str, object]] | None = None,
) -> None:
  """Route fetch_all's HTTP through `handler`, drop the random delay, and
  keep the feed cache in `cache` (keyed by URL) instead of on disk."""
  store = cache if cache is not None else {}
  monkeypatch.setattr(
    feeds,
    "SafeClient",
    partial(SafeClient, transport=httpx.MockTransport(handler), resolver=resolve_public),
  )

  # Replace feeds' own `random` name, not the shared module, so the
  # scheduler's jitter elsewhere is unaffected.
  def no_delay(_a: float, _b: float) -> float:
    return 0

  monkeypatch.setattr(feeds, "random", SimpleNamespace(uniform=no_delay))
  monkeypatch.setattr(feeds, "load_feed_cache", store.get)

  def save(url: str, etag: str | None, last_modified: str | None, body: str) -> None:
    # Same shape as cache.save_feed_cache, so the content-hash check works.
    store[url] = {
      "etag": etag,
      "last_modified": last_modified,
      "body": body,
      "content_hash": hashlib.sha256(body.encode()).hexdigest(),
    }

  monkeypatch.setattr(feeds, "save_feed_cache", save)


def by_host(responses: dict[str, httpx.Response]) -> Callable[[httpx.Request], httpx.Response]:
  """A handler answering each feed by its host's first label ("ok" for ok.example.com)."""

  def handler(request: httpx.Request) -> httpx.Response:
    return responses[request.url.host.split(".")[0]]

  return handler


def rss(*items: tuple[str, datetime | None]) -> str:
  """An RSS 2.0 body with one <item> per (url, published) pair."""
  parts: list[str] = []
  for url, published in items:
    pub = f"<pubDate>{format_datetime(published)}</pubDate>" if published else ""
    parts.append(f"<item><title>T {url}</title><link>{url}</link>{pub}</item>")
  return (
    '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
    f"<title>F</title><link>https://example.com/</link>{''.join(parts)}</channel></rss>"
  )
