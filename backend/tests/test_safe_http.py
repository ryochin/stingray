"""Tests for the guarded outbound HTTP client (SSRF guard and size cap)."""

from __future__ import annotations

import asyncio
import gzip
import socket
from collections.abc import AsyncIterator, Callable
from functools import partial

import httpx
import pydantic
import pytest
from fastapi.testclient import TestClient

import app as app_module
import feeds
import safe_http
from safe_http import (
  BlockedDestinationError,
  FetchSettings,
  ResponseTooLargeError,
  SafeClient,
)
from schemas import AppConfig

PUBLIC_IP = "93.184.216.34"
_RSS = (
  '<?xml version="1.0"?><rss version="2.0"><channel><title>Meta</title>'
  "<link>https://example.com/</link>"
  "<item><title>a</title><link>https://example.com/a</link></item>"
  "</channel></rss>"
)
Handler = Callable[[httpx.Request], httpx.Response]


def _settings(*allow: str) -> FetchSettings:
  return FetchSettings.from_config(
    AppConfig.model_validate({"fetch": {"allow_private": list(allow)}})
  )


def _resolver(table: dict[str, list[str]]) -> safe_http.Resolver:
  async def resolve(host: str, _port: int) -> list[str]:
    if host not in table:
      raise socket.gaierror(socket.EAI_NONAME, "unknown host")
    return table[host]

  return resolve


def _ok(_request: httpx.Request) -> httpx.Response:
  return httpx.Response(200, text="ok")


def _fetch(
  url: str,
  *,
  handler: Handler = _ok,
  dns: dict[str, list[str]] | None = None,
  allow: tuple[str, ...] = (),
  real_dns: bool = False,
  max_bytes: int | None = None,
  truncate: bool = False,
) -> httpx.Response:
  async def run() -> httpx.Response:
    async with SafeClient(
      _settings(*allow),
      timeout=5,
      transport=httpx.MockTransport(handler),
      resolver=None if real_dns else _resolver(dns or {}),
    ) as client:
      return await client.get(url, max_bytes=max_bytes, truncate=truncate)

  return asyncio.run(run())


# -- destination guard --


def test_public_host_is_fetched():
  resp = _fetch("https://example.com/feed", dns={"example.com": [PUBLIC_IP]})
  assert resp.status_code == 200
  assert resp.text == "ok"


@pytest.mark.parametrize(
  "address",
  [
    "127.0.0.1",
    "10.0.0.5",
    "172.16.0.1",
    "192.168.1.10",
    "169.254.169.254",
    "100.64.0.1",
    "0.0.0.0",
    "224.0.0.1",
    "::1",
    "fe80::1%eth0",
    "fc00::1",
    "::ffff:127.0.0.1",
    "64:ff9b::7f00:1",
  ],
)
def test_non_public_resolution_is_blocked(address: str):
  with pytest.raises(BlockedDestinationError):
    _fetch("http://evil.example/", dns={"evil.example": [address]})


@pytest.mark.parametrize(
  "url",
  [
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]:20080/api/health",
    "http://127.0.0.1:5432/",
  ],
)
def test_ip_literal_destinations_are_blocked(url: str):
  with pytest.raises(BlockedDestinationError):
    _fetch(url)


@pytest.mark.parametrize("url", ["http://localhost:20080/", "http://2130706433/"])
def test_real_resolver_blocks_loopback_spellings(url: str):
  with pytest.raises(BlockedDestinationError):
    _fetch(url, real_dns=True)


def test_any_private_address_in_the_answer_blocks():
  with pytest.raises(BlockedDestinationError):
    _fetch("http://mixed.example/", dns={"mixed.example": [PUBLIC_IP, "10.0.0.1"]})


@pytest.mark.parametrize("url", ["ftp://example.com/feed", "file:///etc/passwd"])
def test_non_http_schemes_are_blocked(url: str):
  with pytest.raises(BlockedDestinationError):
    _fetch(url, dns={"example.com": [PUBLIC_IP]})


def test_allowed_network_opens_a_private_range():
  resp = _fetch("http://nas.lan/feed", dns={"nas.lan": ["192.168.1.20"]}, allow=("192.168.1.0/24",))
  assert resp.status_code == 200


def test_allowed_network_does_not_open_other_ranges():
  with pytest.raises(BlockedDestinationError):
    _fetch("http://nas.lan/feed", dns={"nas.lan": ["192.168.2.20"]}, allow=("192.168.1.0/24",))


@pytest.mark.parametrize(
  ("allowed", "url"),
  [
    ("bücher.local", "http://xn--bcher-kva.local/feed"),
    ("xn--bcher-kva.local", "http://bücher.local/feed"),
  ],
)
def test_allowed_hostname_matches_across_idna_forms(allowed: str, url: str):
  assert _fetch(url, allow=(allowed,)).status_code == 200


def test_bracketed_ipv6_entry_allows_that_address():
  assert _fetch("http://[fd00::5]/feed", allow=("[fd00::5]",)).status_code == 200


def test_allowed_hostname_skips_the_address_check():
  # No DNS entry: an allowed hostname must not even be resolved.
  resp = _fetch("http://NAS.local/feed", allow=("nas.local",))
  assert resp.status_code == 200


def test_redirect_to_a_private_address_is_blocked():
  seen: list[str] = []

  def handler(request: httpx.Request) -> httpx.Response:
    seen.append(request.url.host)
    return httpx.Response(302, headers={"location": "http://internal.example/admin"})

  with pytest.raises(BlockedDestinationError):
    _fetch(
      "https://example.com/feed",
      handler=handler,
      dns={"example.com": [PUBLIC_IP], "internal.example": ["10.0.0.7"]},
    )
  assert seen == ["example.com"]


def test_redirect_is_followed_and_url_is_the_final_one():
  def handler(request: httpx.Request) -> httpx.Response:
    if request.url.host == "example.com":
      return httpx.Response(301, headers={"location": "https://www.example.com/rss"})
    return httpx.Response(200, text="final")

  resp = _fetch(
    "https://example.com/feed",
    handler=handler,
    dns={"example.com": [PUBLIC_IP], "www.example.com": [PUBLIC_IP]},
  )
  assert resp.text == "final"
  assert str(resp.url) == "https://www.example.com/rss"


def test_redirect_body_is_never_read():
  read: list[int] = []

  async def huge_body() -> AsyncIterator[bytes]:
    read.append(1)
    yield b"x" * 1024

  def handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/feed":
      return httpx.Response(302, headers={"location": "/next"}, content=huge_body())
    return httpx.Response(200, text="ok")

  resp = _fetch("https://example.com/feed", handler=handler, dns={"example.com": [PUBLIC_IP]})
  assert resp.text == "ok"
  assert read == []


def test_redirect_loop_is_bounded():
  def handler(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(302, headers={"location": "/again"})

  with pytest.raises(httpx.TooManyRedirects):
    _fetch("https://example.com/", handler=handler, dns={"example.com": [PUBLIC_IP]})


def test_dns_failure_is_a_connect_error():
  with pytest.raises(httpx.ConnectError):
    _fetch("https://no-such-host.example/")


def test_empty_dns_answer_is_a_connect_error() -> None:
  # With no address to check, the guard would otherwise let the request through.
  served: list[str] = []

  def handler(request: httpx.Request) -> httpx.Response:
    served.append(str(request.url))
    return httpx.Response(200, text="ok")

  with pytest.raises(httpx.ConnectError, match="no address"):
    _fetch("https://example.com/", handler=handler, dns={"example.com": []})
  assert served == []


def test_dns_timeout_is_a_connect_timeout(monkeypatch: pytest.MonkeyPatch):
  monkeypatch.setattr(safe_http, "DNS_TIMEOUT_SECONDS", 0.01)

  async def slow(_host: str, _port: int) -> list[str]:
    await asyncio.sleep(1)
    return [PUBLIC_IP]

  async def run() -> None:
    async with SafeClient(
      _settings(),
      timeout=5,
      transport=httpx.MockTransport(_ok),
      resolver=slow,
    ) as client:
      await client.get("https://example.com/")

  with pytest.raises(httpx.ConnectTimeout):
    asyncio.run(run())


# -- size cap --

_DNS = {"example.com": [PUBLIC_IP]}


def _chunked(chunks: int, size: int, served: list[int]) -> Handler:
  async def body() -> AsyncIterator[bytes]:
    for _ in range(chunks):
      served.append(1)
      yield b"x" * size

  def handler(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=body())

  return handler


def test_declared_length_over_the_limit_is_rejected_unread():
  served: list[int] = []
  streaming = _chunked(20, 100, served)

  def handler(request: httpx.Request) -> httpx.Response:
    resp = streaming(request)
    resp.headers["content-length"] = "2000"
    return resp

  with pytest.raises(ResponseTooLargeError):
    _fetch("https://example.com/", handler=handler, dns=_DNS, max_bytes=1000)
  assert served == []


def test_undeclared_body_is_cut_off_mid_stream():
  served: list[int] = []
  with pytest.raises(ResponseTooLargeError):
    _fetch("https://example.com/", handler=_chunked(100, 100, served), dns=_DNS, max_bytes=1000)
  assert len(served) < 100


def test_body_within_the_limit_is_returned_whole():
  served: list[int] = []
  resp = _fetch("https://example.com/", handler=_chunked(10, 100, served), dns=_DNS, max_bytes=1000)
  assert resp.content == b"x" * 1000


def test_truncate_cuts_a_declared_oversized_body():
  def handler(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=b"x" * 2000)

  resp = _fetch("https://example.com/", handler=handler, dns=_DNS, max_bytes=1000, truncate=True)
  assert resp.content == b"x" * 1000


def test_truncate_cuts_an_undeclared_oversized_body():
  served: list[int] = []
  resp = _fetch(
    "https://example.com/",
    handler=_chunked(100, 300, served),
    dns=_DNS,
    max_bytes=1000,
    truncate=True,
  )
  assert resp.content == b"x" * 1000
  assert len(served) < 100


def _compressed_stream(encoding: str, payload: bytes, served: list[int] | None = None) -> Handler:
  # Stream the compressed bytes in small pieces so decoding happens on the
  # same incremental path as a real network response.
  async def body() -> AsyncIterator[bytes]:
    for i in range(0, len(payload), 512):
      if served is not None:
        served.append(1)
      yield payload[i : i + 512]

  def handler(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, headers={"content-encoding": encoding}, content=body())

  return handler


def test_limit_applies_to_the_decompressed_size():
  compressed = gzip.compress(b"x" * 200_000)
  assert len(compressed) < 100_000
  with pytest.raises(ResponseTooLargeError):
    _fetch(
      "https://example.com/",
      handler=_compressed_stream("gzip", compressed),
      dns=_DNS,
      max_bytes=100_000,
    )


@pytest.mark.parametrize("encoding", ["gzip, gzip", "x-gzip", "br"])
def test_unsupported_encodings_are_refused_before_reading(encoding: str):
  served: list[int] = []
  payload = gzip.compress(gzip.compress(b"x" * 1000))
  with pytest.raises(httpx.DecodingError):
    _fetch(
      "https://example.com/",
      handler=_compressed_stream(encoding, payload, served),
      dns=_DNS,
    )
  assert served == []


@pytest.mark.parametrize("encoding", ["GZIP", "gzip, identity", "identity, gzip"])
def test_single_compression_layer_is_decoded(encoding: str):
  payload = gzip.compress(b"hello")
  resp = _fetch("https://example.com/", handler=_compressed_stream(encoding, payload), dns=_DNS)
  assert resp.content == b"hello"


def test_truncate_stops_reading_once_the_limit_is_met_exactly():
  async def body() -> AsyncIterator[bytes]:
    yield b"x" * 1000
    raise httpx.ReadTimeout("stalled after the limit")

  def handler(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=body())

  resp = _fetch("https://example.com/", handler=handler, dns=_DNS, max_bytes=1000, truncate=True)
  assert resp.content == b"x" * 1000


def test_decoded_body_keeps_its_charset():
  def handler(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(
      200,
      headers={"content-type": "text/html; charset=shift_jis", "content-encoding": "gzip"},
      content=gzip.compress("日本語".encode("shift_jis")),
    )

  resp = _fetch("https://example.com/", handler=handler, dns=_DNS)
  assert resp.text == "日本語"


# -- config --


@pytest.mark.parametrize(
  "entry",
  [
    "",
    "  ",
    "10.0.0.0/33",
    "not-a-cidr/8",
    "nas:8080",
    ":80",
    "user@nas",
    "nas?x",
    "[nas]",
  ],
)
def test_invalid_allow_private_entries_are_rejected(entry: str):
  with pytest.raises(pydantic.ValidationError):
    AppConfig.model_validate({"fetch": {"allow_private": [entry]}})


# -- integration with callers --


def test_blocked_feed_does_not_fall_back_to_the_cache(monkeypatch: pytest.MonkeyPatch):
  monkeypatch.setattr(feeds, "load_feed_cache", lambda _url: {"body": "<rss/>"})

  async def run() -> tuple[str, bool, str]:
    async with SafeClient(
      _settings(),
      timeout=5,
      transport=httpx.MockTransport(_ok),
      resolver=_resolver({"internal.example": ["10.0.0.7"]}),
    ) as client:
      return await feeds._fetch_with_cache(client, "http://internal.example/feed")

  with pytest.raises(BlockedDestinationError):
    asyncio.run(run())


def test_not_modified_is_served_from_the_cache_whatever_its_length(
  monkeypatch: pytest.MonkeyPatch,
):
  # A 304's Content-Length describes the cached resource, not a transfer.
  monkeypatch.setattr(feeds, "load_feed_cache", lambda _url: {"body": "<rss/>", "etag": "e"})

  def handler(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(304, headers={"content-length": str(20 * 1024 * 1024)})

  async def run() -> tuple[str, bool, str]:
    async with SafeClient(
      _settings(),
      timeout=5,
      transport=httpx.MockTransport(handler),
      resolver=_resolver(_DNS),
    ) as client:
      return await feeds._fetch_with_cache(client, "https://example.com/feed")

  assert asyncio.run(run()) == ("<rss/>", True, "304")


def test_dns_failure_still_falls_back_to_the_cache(monkeypatch: pytest.MonkeyPatch):
  monkeypatch.setattr(feeds, "load_feed_cache", lambda _url: {"body": "<rss/>"})

  async def run() -> tuple[str, bool, str]:
    async with SafeClient(
      _settings(),
      timeout=5,
      transport=httpx.MockTransport(_ok),
      resolver=_resolver({}),
    ) as client:
      return await feeds._fetch_with_cache(client, "https://gone.example/feed")

  assert asyncio.run(run()) == ("<rss/>", True, "net-cache")


def test_adding_a_feed_on_a_private_address_is_refused(monkeypatch: pytest.MonkeyPatch):
  # The transport would serve a valid feed, so a 422 can only come from the
  # guard refusing to send the request at all.
  served: list[str] = []

  def handler(request: httpx.Request) -> httpx.Response:
    served.append(str(request.url))
    return httpx.Response(200, text=_RSS, headers={"content-type": "application/rss+xml"})

  monkeypatch.setattr(
    app_module,
    "SafeClient",
    partial(SafeClient, transport=httpx.MockTransport(handler)),
  )
  app_module.app.state.config = AppConfig()
  resp = TestClient(app_module.app).post(
    "/api/feeds",
    json={"name": "", "url": "http://169.254.169.254/latest/meta-data/"},
  )
  assert resp.status_code == 422
  assert served == []
