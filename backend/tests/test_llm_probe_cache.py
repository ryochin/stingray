"""Guards for the Ollama reachability probe cache in app.py.

/api/status is polled as often as every 2s while a fetch is running, and the
probe blocks for as long as Ollama takes to answer. These tests pin the two
properties that keep a slow Ollama from multiplying into many concurrent
probes: the TTL cache, and the in-flight de-duplication that serves a stale
result rather than starting a second probe.

No network — _probe_llm_once is always monkeypatched.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import pytest

import app

URL = "http://ollama.test:11434"

# Captured before any monkeypatching so _RecordingEvent can build its own
# Event without recursing into itself.
_RealEvent = threading.Event


class _RecordingEvent(threading.Event):
  """Event that reports when some thread has actually entered wait().

  Lets the concurrency tests synchronise on "the second caller has reached the
  shared-probe path" instead of sleeping and hoping.
  """

  def __init__(self) -> None:
    super().__init__()
    self.waiting = _RealEvent()

  def wait(self, timeout: float | None = None) -> bool:
    self.waiting.set()
    return super().wait(timeout)


@pytest.fixture(autouse=True)
def clean_probe_state():
  """Each test starts with an empty cache and no in-flight probes."""
  app._llm_probe_cache.clear()
  app._llm_probe_inflight.clear()
  yield
  app._llm_probe_cache.clear()
  app._llm_probe_inflight.clear()


def _stub_once(
  monkeypatch: pytest.MonkeyPatch, fn: Callable[[str], tuple[bool, str | None]]
) -> None:
  monkeypatch.setattr(app, "_probe_llm_once", fn)


class TestTtlCache:
  def test_probes_on_first_call(self, monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    def once(base_url: str) -> tuple[bool, str | None]:
      calls.append(base_url)
      return True, None

    _stub_once(monkeypatch, once)
    assert app._probe_llm(URL) == (True, None)
    assert calls == [URL]

  def test_second_call_within_ttl_uses_cache(self, monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    def once(base_url: str) -> tuple[bool, str | None]:
      calls.append(base_url)
      return True, None

    _stub_once(monkeypatch, once)
    app._probe_llm(URL)
    app._probe_llm(URL)
    assert len(calls) == 1

  def test_expired_cache_probes_again(self, monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    def once(base_url: str) -> tuple[bool, str | None]:
      calls.append(base_url)
      return True, None

    _stub_once(monkeypatch, once)
    app._probe_llm(URL)
    # Force expiry by rewriting the deadline rather than sleeping.
    _, ok, err = app._llm_probe_cache[URL]
    app._llm_probe_cache[URL] = (0.0, ok, err)
    app._probe_llm(URL)
    assert len(calls) == 2

  def test_failure_retries_up_to_the_attempt_limit(self, monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    def once(base_url: str) -> tuple[bool, str | None]:
      calls.append(base_url)
      return False, "ConnectError: refused"

    _stub_once(monkeypatch, once)
    ok, err = app._probe_llm(URL)
    assert (ok, err) == (False, "ConnectError: refused")
    assert len(calls) == app._LLM_PROBE_ATTEMPTS

  def test_retry_stops_at_first_success(self, monkeypatch: pytest.MonkeyPatch):
    results: list[tuple[bool, str | None]] = [
      (False, "timeout"), (True, None), (True, None),
    ]
    calls: list[str] = []

    def once(base_url: str) -> tuple[bool, str | None]:
      calls.append(base_url)
      return results[len(calls) - 1]

    _stub_once(monkeypatch, once)
    assert app._probe_llm(URL) == (True, None)
    assert len(calls) == 2

  def test_ttl_runs_from_completion_not_entry(self, monkeypatch: pytest.MonkeyPatch):
    """A probe that took longer than the TTL must not expire on arrival."""
    entry = 1000.0
    completion = entry + app._LLM_PROBE_TTL + 5.0
    ticks = iter([entry, completion])
    monkeypatch.setattr(app.time, "monotonic", lambda: next(ticks))
    _stub_once(monkeypatch, lambda _: (True, None))

    app._probe_llm(URL)
    deadline, _, _ = app._llm_probe_cache[URL]
    # Exact: completion + TTL. Storing the bare completion time, or basing the
    # deadline on entry, both have to fail here.
    assert deadline == completion + app._LLM_PROBE_TTL

  def test_result_cached_at_completion_is_reused(self, monkeypatch: pytest.MonkeyPatch):
    """Companion to the above: the deadline must actually be honoured."""
    calls: list[str] = []

    def once(base_url: str) -> tuple[bool, str | None]:
      calls.append(base_url)
      return False, "ConnectError: refused"

    _stub_once(monkeypatch, once)
    first = app._probe_llm(URL)
    assert app._probe_llm(URL) == first
    assert len(calls) == app._LLM_PROBE_ATTEMPTS  # one probe, not two


class _SuspendedProbe:
  """Runs _probe_llm on a background thread and parks it inside the stubbed
  _probe_llm_once until released, so assertions can run while it is in flight.

  Always joins the thread on exit — a probe left running past the fixture
  teardown would write into the next test's cache.
  """

  def __init__(self, monkeypatch: pytest.MonkeyPatch, result: tuple[bool, str | None]):
    self._monkeypatch = monkeypatch
    self._result = result
    self._release = threading.Event()
    self._entered = threading.Event()
    self.calls: list[str] = []
    self.returned: dict[str, Any] = {}
    self._thread: threading.Thread | None = None

  def _once(self, base_url: str) -> tuple[bool, str | None]:
    self.calls.append(base_url)
    # Only the endpoint under suspension parks; probes for any other base_url
    # return straight away so they can be used to prove independence.
    if base_url == URL:
      self._entered.set()
      self._release.wait(timeout=5)
    return self._result

  def __enter__(self) -> _SuspendedProbe:
    _stub_once(self._monkeypatch, self._once)
    self._thread = threading.Thread(
      target=lambda: self.returned.update(value=app._probe_llm(URL))
    )
    self._thread.start()
    if not self._entered.wait(timeout=5):
      # __exit__ does not run when __enter__ raises, so reap the thread here.
      self.release()
      raise AssertionError("the probe never started")
    return self

  def release(self) -> None:
    self._release.set()
    if self._thread is not None:
      self._thread.join(timeout=5)
      assert not self._thread.is_alive(), "the probe thread did not finish"

  def __exit__(self, *_exc: object) -> None:
    self.release()


class TestInflightDeduplication:
  def test_concurrent_caller_gets_stale_result_instead_of_probing(
    self, monkeypatch: pytest.MonkeyPatch
  ):
    """Simulates an Ollama that became unreachable after a successful probe:
    the cached value is stale AND expired, but a caller arriving mid-probe has
    to reuse it rather than parking a thread on the slow endpoint."""
    app._llm_probe_cache[URL] = (0.0, True, None)  # expired "available"

    with _SuspendedProbe(monkeypatch, (False, "ReadTimeout")) as probe:
      # Must return immediately with the stale value — not block, not probe.
      assert app._probe_llm(URL) == (True, None)
      assert len(probe.calls) == 1

    assert probe.returned["value"] == (False, "ReadTimeout")

  def test_concurrent_first_caller_shares_the_running_probe(
    self, monkeypatch: pytest.MonkeyPatch
  ):
    """The case that matters when Ollama is down at startup: nothing is cached,
    so there is no stale value to serve. The second caller must wait for the
    running probe and reuse its result instead of starting a second one.

    This is the worst case, not a rare one — /api/status polls every 2s while a
    fetch runs, so a 15s probe would otherwise stack up threads.
    """
    monkeypatch.setattr(app.threading, "Event", _RecordingEvent)

    with _SuspendedProbe(monkeypatch, (False, "ConnectTimeout")) as probe:
      running = app._llm_probe_inflight[URL]
      assert isinstance(running, _RecordingEvent)

      second: dict[str, Any] = {}
      waiter = threading.Thread(target=lambda: second.update(value=app._probe_llm(URL)))
      waiter.start()
      try:
        # Deterministic: proceed only once the waiter is genuinely parked on the
        # owner's event, so a waiter that merely hit a warm cache cannot pass.
        assert running.waiting.wait(timeout=5), (
          "the second caller never reached the shared-probe path"
        )
        assert len(probe.calls) == 1, "a second probe was started"
        assert not second, "the second caller should still be waiting"
      finally:
        probe.release()
        waiter.join(timeout=5)

      assert not waiter.is_alive(), "the waiting caller never woke up"
      assert second["value"] == (False, "ConnectTimeout")
      # Exactly the owner's own retry sequence and nothing more — the waiter
      # contributed no probe of its own.
      assert probe.calls == [URL] * app._LLM_PROBE_ATTEMPTS

  def test_inflight_marker_is_cleared_after_a_probe(self, monkeypatch: pytest.MonkeyPatch):
    _stub_once(monkeypatch, lambda _: (True, None))
    app._probe_llm(URL)
    assert URL not in app._llm_probe_inflight

  def test_inflight_marker_is_cleared_when_the_probe_raises(
    self, monkeypatch: pytest.MonkeyPatch
  ):
    """_probe_llm_once swallows exceptions today, but a leaked marker would
    wedge the endpoint into permanently serving stale results."""

    def boom(base_url: str) -> tuple[bool, str | None]:
      raise RuntimeError("unexpected")

    _stub_once(monkeypatch, boom)
    with pytest.raises(RuntimeError):
      app._probe_llm(URL)
    assert URL not in app._llm_probe_inflight

  def test_distinct_endpoints_do_not_block_each_other(self, monkeypatch: pytest.MonkeyPatch):
    """De-duplication is per endpoint. A probe parked on one base_url must not
    hold up an unrelated one — a global lock around the probe would fail here.
    """
    other = "http://other.test:11434"

    with _SuspendedProbe(monkeypatch, (False, "ReadTimeout")) as probe:
      done = threading.Event()
      result: dict[str, Any] = {}

      def probe_other() -> None:
        result["value"] = app._probe_llm(other)
        done.set()

      thread = threading.Thread(target=probe_other)
      thread.start()
      try:
        assert done.wait(timeout=2), "the second endpoint was blocked by the first"
      finally:
        # Release the owner first: if the assertion above failed because the
        # other endpoint really was blocked, this is what lets it finish.
        probe.release()
        thread.join(timeout=5)
        assert not thread.is_alive(), "the second endpoint's probe never finished"

      assert result["value"] == (False, "ReadTimeout")
      # The parked endpoint was probed first; the other one then ran its full
      # retry sequence to completion while that one was still held. Counted
      # rather than compared as a list, because releasing the owner above lets
      # it resume its own retries and append more entries.
      assert probe.calls[0] == URL
      assert probe.calls.count(other) == app._LLM_PROBE_ATTEMPTS
