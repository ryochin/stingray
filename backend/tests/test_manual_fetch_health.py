"""Guards app._manual_success_health: a successful manual single-feed fetch
must classify a served stale cache as "degraded" (with the shared diagnostic),
mirroring the scheduled path, while a genuinely fresh/unchanged fetch is "ok".
"""

from __future__ import annotations

import app
from fetcher import STALE_CACHE_DIAGNOSTICS


class TestManualSuccessHealth:
  def test_fresh_body_is_ok(self):
    assert app._manual_success_health(None) == ("ok", None)

  def test_unchanged_is_ok(self):
    # A clean conditional 304 that reused a valid cache is healthy.
    assert app._manual_success_health("unchanged") == ("ok", None)

  def test_5xx_cache_is_degraded(self):
    health, error = app._manual_success_health("5xx-cache")
    assert health == "degraded"
    assert error == STALE_CACHE_DIAGNOSTICS["5xx-cache"]

  def test_net_cache_is_degraded(self):
    health, error = app._manual_success_health("net-cache")
    assert health == "degraded"
    assert error == STALE_CACHE_DIAGNOSTICS["net-cache"]

  def test_304_empty_is_degraded(self):
    health, error = app._manual_success_health("304-empty")
    assert health == "degraded"
    assert error == STALE_CACHE_DIAGNOSTICS["304-empty"]
