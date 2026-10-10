"""Outbound HTTP for user-supplied URLs (feeds and web pages).

Feed URLs arrive through an unauthenticated API, so every fetch of one goes
through `SafeClient`, which:

- refuses destinations whose scheme is not http(s) or whose host resolves to a
  non-public address (SSRF), re-checked on every redirect hop, and
- caps the decoded response body, including the bodies of redirect responses
  (it follows redirects itself so intermediate bodies are never read).

Ollama traffic deliberately bypasses this module: its base URL is operator
config and normally points at a private address.

Known limits: the address is checked before httpx connects, so DNS rebinding
between the check and the connect is not prevented; behind an HTTP proxy the
proxy does its own resolution.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from types import TracebackType
from typing import Awaitable, Callable

import httpx

from schemas import AppConfig

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
Resolver = Callable[[str, int], Awaitable[list[str]]]

MAX_REDIRECTS = 10
DNS_TIMEOUT_SECONDS = 10.0

# Well-known NAT64 prefix: `is_global` is true for it, but a translator would
# forward to the embedded IPv4 address, which must be judged instead.
_NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")
# httpx decompresses a whole chunk before the size cap sees it. A single
# gzip/deflate layer bounds that transient expansion to roughly 1000x a network
# chunk; brotli, zstd, and stacked encodings ("gzip, gzip") would not, so they
# are neither requested nor accepted.
_ACCEPT_ENCODING = "gzip, deflate"
_COMPRESSIONS = frozenset({"gzip", "deflate"})
# Statuses that never carry a body; their Content-Length describes the
# resource, not the transfer, so the body checks do not apply.
_BODYLESS_STATUSES = frozenset({204, 304})
# Wire-level headers dropped when the decoded body is repackaged, so httpx does
# not decode it a second time or trust a stale length.
_WIRE_HEADERS = frozenset({"content-encoding", "content-length", "transfer-encoding"})


class BlockedDestinationError(httpx.RequestError):
  """The URL's scheme or resolved address is not allowed."""


class ResponseTooLargeError(httpx.RequestError):
  """The response body exceeded the size limit."""


@dataclass(frozen=True)
class FetchSettings:
  user_agent: str
  allowed_hosts: frozenset[str]
  allowed_networks: tuple[IPNetwork, ...]
  max_response_bytes: int

  @classmethod
  def from_config(cls, config: AppConfig) -> FetchSettings:
    hosts: set[str] = set()
    networks: list[IPNetwork] = []
    for entry in config.fetch.allow_private:
      try:
        networks.append(ipaddress.ip_network(entry, strict=False))
      except ValueError:
        hosts.add(entry)
    return cls(
      user_agent=config.user_agent,
      allowed_hosts=frozenset(hosts),
      allowed_networks=tuple(networks),
      max_response_bytes=config.fetch.max_response_bytes,
    )


async def _resolve(host: str, port: int) -> list[str]:
  loop = asyncio.get_running_loop()
  infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
  return [str(info[4][0]) for info in infos]


def _effective_address(ip: IPAddress) -> IPAddress:
  """Unwrap IPv6 forms that carry an IPv4 destination."""
  if isinstance(ip, ipaddress.IPv6Address):
    if ip.ipv4_mapped is not None:
      return ip.ipv4_mapped
    if ip in _NAT64_PREFIX:
      return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
  return ip


def _is_public(ip: IPAddress) -> bool:
  return ip.is_global and not ip.is_multicast


class SafeClient:
  """httpx.AsyncClient wrapper exposing only a guarded, size-capped GET."""

  def __init__(
    self,
    settings: FetchSettings,
    *,
    timeout: float | httpx.Timeout,
    limits: httpx.Limits | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    resolver: Resolver | None = None,
  ) -> None:
    self._settings = settings
    self._resolver: Resolver = resolver or _resolve
    self._client = httpx.AsyncClient(
      timeout=timeout,
      follow_redirects=False,
      limits=limits or httpx.Limits(max_connections=100, max_keepalive_connections=20),
      headers={"User-Agent": settings.user_agent, "Accept-Encoding": _ACCEPT_ENCODING},
      event_hooks={"request": [self._guard]},
      transport=transport,
    )

  async def __aenter__(self) -> SafeClient:
    await self._client.__aenter__()
    return self

  async def __aexit__(
    self,
    exc_type: type[BaseException] | None,
    exc: BaseException | None,
    tb: TracebackType | None,
  ) -> None:
    await self._client.__aexit__(exc_type, exc, tb)

  async def get(
    self,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    max_bytes: int | None = None,
    truncate: bool = False,
  ) -> httpx.Response:
    """GET `url`, following redirects, with the body fully read.

    The body is capped at `max_bytes` (default: the configured limit). Over
    the cap, ResponseTooLargeError is raised, or with `truncate` the body is
    cut at the cap instead. The returned response carries the final request,
    so `.url` is the post-redirect URL.
    """
    limit = max_bytes or self._settings.max_response_bytes
    request = self._client.build_request("GET", url, headers=headers)
    for _ in range(MAX_REDIRECTS + 1):
      response = await self._client.send(request, stream=True)
      try:
        next_request = response.next_request
        if next_request is None:
          return await _read_limited(response, limit, truncate=truncate)
      finally:
        await response.aclose()
      request = next_request
    raise httpx.TooManyRedirects(f"Exceeded {MAX_REDIRECTS} redirects", request=request)

  async def _guard(self, request: httpx.Request) -> None:
    url = request.url
    if url.scheme not in ("http", "https"):
      raise BlockedDestinationError(f"Scheme not allowed: {url.scheme}", request=request)
    # raw_host is the IDNA (ASCII) form, the same form allow_private is stored in.
    host = url.raw_host.decode("ascii").lower()
    if not host:
      raise BlockedDestinationError("URL has no host", request=request)
    if host in self._settings.allowed_hosts:
      return
    try:
      addresses: list[IPAddress] = [ipaddress.ip_address(host)]
    except ValueError:
      addresses = await self._lookup(request)
    for ip in addresses:
      if not self._is_allowed(ip):
        raise BlockedDestinationError(
          f"Destination not allowed: {host} resolves to non-public address {ip}",
          request=request,
        )

  async def _lookup(self, request: httpx.Request) -> list[IPAddress]:
    url = request.url
    host = url.raw_host.decode("ascii")
    port = url.port or (443 if url.scheme == "https" else 80)
    try:
      raw = await asyncio.wait_for(self._resolver(host, port), DNS_TIMEOUT_SECONDS)
    except TimeoutError as e:
      raise httpx.ConnectTimeout(f"DNS lookup timed out: {host}", request=request) from e
    except OSError as e:
      raise httpx.ConnectError(f"DNS lookup failed: {host}: {e}", request=request) from e
    if not raw:
      raise httpx.ConnectError(f"DNS lookup returned no address: {host}", request=request)
    # Link-local IPv6 results may carry a zone id ("fe80::1%eth0").
    return [ipaddress.ip_address(a.split("%", 1)[0]) for a in raw]

  def _is_allowed(self, ip: IPAddress) -> bool:
    effective = _effective_address(ip)
    if _is_public(effective):
      return True
    return any(
      addr in net
      for addr in {ip, effective}
      for net in self._settings.allowed_networks
      if addr.version == net.version
    )


def _check_content_encoding(response: httpx.Response) -> None:
  """Refuse encodings that httpx would skip or that stack compression.

  Mirrors how httpx picks decoders (comma-split, case-insensitive): an unknown
  token would be passed through undecoded, and more than one compression layer
  would void the expansion bound above.
  """
  tokens = [
    t.strip().lower()
    for t in response.headers.get_list("content-encoding", split_commas=True)
  ]
  layers = [t for t in tokens if t and t != "identity"]
  if any(t not in _COMPRESSIONS for t in layers) or len(layers) > 1:
    raise httpx.DecodingError(
      f"Unsupported content encoding: {', '.join(tokens)}", request=response.request,
    )


async def _read_limited(response: httpx.Response, limit: int, *, truncate: bool) -> httpx.Response:
  if response.status_code not in _BODYLESS_STATUSES:
    _check_content_encoding(response)
  declared = response.headers.get("content-length", "")
  if (
    not truncate
    and response.status_code not in _BODYLESS_STATUSES
    and declared.isdigit()
    and int(declared) > limit
  ):
    raise ResponseTooLargeError(
      f"Response is {declared} bytes, over the {limit}-byte limit", request=response.request,
    )
  chunks: list[bytes] = []
  size = 0
  async for chunk in response.aiter_bytes():
    remaining = limit - size
    if len(chunk) > remaining:
      if not truncate:
        raise ResponseTooLargeError(
          f"Response exceeds the {limit}-byte limit", request=response.request,
        )
      chunks.append(chunk[:remaining])
      break
    chunks.append(chunk)
    size += len(chunk)
    if truncate and size == limit:
      break
  headers = [(k, v) for k, v in response.headers.multi_items() if k.lower() not in _WIRE_HEADERS]
  return httpx.Response(
    response.status_code,
    headers=headers,
    content=b"".join(chunks),
    request=response.request,
    extensions=response.extensions,
  )
