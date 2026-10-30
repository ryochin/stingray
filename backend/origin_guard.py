"""Cross-origin request protection (CSRF) for the unauthenticated API.

Several endpoints change state from a bodiless POST (mark all read, refresh),
so any page the user visits could fire them with a form or a no-cors fetch.
This rejects unsafe requests that a browser marks as coming from another
origin, following Go 1.25's net/http.CrossOriginProtection:

1. GET, HEAD and OPTIONS pass.
2. Sec-Fetch-Site "same-origin" or "none" passes; any other non-empty value
   (including "same-site", which covers other ports on localhost) is rejected.
3. Without Sec-Fetch-Site, a request without Origin passes (curl, scripts,
   healthchecks). Otherwise the host[:port] of Origin must equal the Host
   header exactly; the scheme is not compared.

Browsers send Sec-Fetch-Site only to trustworthy URLs (HTTPS or localhost), so
over plain HTTP on a LAN address the Origin/Host comparison is what decides.
A reverse proxy that rewrites Host would then reject legitimate requests.

DNS rebinding is out of scope here: a rebound page is same-origin to the
browser, so host_guard refuses it by its Host instead.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

import log

_SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})
_ALLOWED_FETCH_SITES: frozenset[str] = frozenset({"same-origin", "none"})


def is_allowed(method: str, sec_fetch_site: str, origin: str, host: str) -> bool:
  """Whether a request may proceed. Missing headers are passed as ""."""
  if method in _SAFE_METHODS:
    return True
  if sec_fetch_site:
    return sec_fetch_site in _ALLOWED_FETCH_SITES
  if not origin:
    return True
  try:
    origin_host: str = urlsplit(origin).netloc
  except ValueError:
    return False
  # Browsers send a serialized origin (scheme://host[:port]), so netloc is
  # enough; Go's full URL parser differs only on inputs browsers never send.
  # Go would also accept an empty Origin host against an empty Host; a request
  # without Host is not worth honoring, so require a non-empty match.
  return origin_host != "" and origin_host == host


class OriginGuardMiddleware:
  """Pure ASGI middleware applying `is_allowed` to every HTTP request.

  It sits outside FastAPI's exception handling, so a rejection is sent as a
  response directly rather than raised as an HTTPException.
  """

  def __init__(self, app: ASGIApp) -> None:
    self.app: ASGIApp = app

  async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
    if scope["type"] != "http":
      await self.app(scope, receive, send)
      return
    # Keep the first of any repeated header, as Go's Header.Get does.
    headers: dict[str, str] = {}
    for key, value in scope["headers"]:
      if key in (b"sec-fetch-site", b"origin", b"host"):
        headers.setdefault(key.decode("latin-1"), value.decode("latin-1"))
    method: str = scope["method"]
    sec_fetch_site: str = headers.get("sec-fetch-site", "")
    origin: str = headers.get("origin", "")
    if is_allowed(method, sec_fetch_site, origin, headers.get("host", "")):
      await self.app(scope, receive, send)
      return
    log.warn(
      f"Blocked cross-origin request: {method} {scope['path']!r} "
      f"(Sec-Fetch-Site={sec_fetch_site!r}, Origin={origin!r})"
    )
    response = JSONResponse({"detail": "Cross-origin request blocked"}, status_code=403)
    await response(scope, receive, send)
