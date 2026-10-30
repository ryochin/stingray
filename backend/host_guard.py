"""DNS rebinding protection for the unauthenticated API.

A rebinding attack points the attacker's own domain at Stingray's address
after the page has loaded, so the browser treats the attacker's script as
same-origin and lets it read and write the API; `origin_guard` cannot tell the
difference. The browser still sends the attacker's domain in Host, though, so
every request (reads included) whose Host is not a name Stingray answers to is
rejected:

1. A request without Host passes: browsers always send it.
2. IP literals pass: rebinding needs a domain name the attacker controls.
3. "localhost" and "*.localhost" pass: browsers resolve them to loopback
   without asking DNS (RFC 6761).
4. Any other hostname must be listed in `web.allowed_hosts` in config.yml.
"""

from __future__ import annotations

import ipaddress

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

import log
from schemas import AppConfig

# Empty in production; the test suite adds Starlette TestClient's "testserver".
EXTRA_ALLOWED_HOSTS: frozenset[str] = frozenset()


def host_name(host: str) -> str:
  """The Host header without its port, lowercased and without a trailing dot."""
  if host.startswith("["):
    # Bracketed IPv6 literal, optionally followed by :port.
    return host[1 : host.find("]")] if "]" in host else host
  name: str = host.rpartition(":")[0] if ":" in host else host
  return name.lower().rstrip(".")


def is_allowed_host(host: str, allowed_hosts: frozenset[str]) -> bool:
  """Whether a request with this Host header may proceed. Missing is ""."""
  if not host:
    return True
  name: str = host_name(host)
  try:
    ipaddress.ip_address(name)
  except ValueError:
    pass
  else:
    return True
  if name == "localhost" or name.endswith(".localhost"):
    return True
  return name in allowed_hosts


class HostGuardMiddleware:
  """Pure ASGI middleware applying `is_allowed_host` to every HTTP request.

  The allowlist comes from the config the lifespan stashes on app.state; before
  that (or in tests that skip the lifespan) only the built-in rules apply.
  """

  def __init__(self, app: ASGIApp) -> None:
    self.app: ASGIApp = app

  async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
    if scope["type"] != "http":
      await self.app(scope, receive, send)
      return
    # Keep the first of any repeated header, as origin_guard does.
    host: str = next(
      (value.decode("latin-1") for key, value in scope["headers"] if key == b"host"), ""
    )
    config: AppConfig | None = getattr(scope["app"].state, "config", None)
    allowed_hosts: frozenset[str] = (
      frozenset(config.web.allowed_hosts) if config is not None else frozenset()
    )
    if is_allowed_host(host, allowed_hosts | EXTRA_ALLOWED_HOSTS):
      await self.app(scope, receive, send)
      return
    log.warn(
      f"Blocked request with unknown Host: {scope['method']} {scope['path']!r} (Host={host!r})"
    )
    response = JSONResponse({"detail": "Host not allowed"}, status_code=403)
    await response(scope, receive, send)
