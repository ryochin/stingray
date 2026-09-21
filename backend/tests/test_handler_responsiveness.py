"""Dynamic guard: one slow handler must not stall every other request.

test_async_blocking.py checks the shape of the code; this checks the effect.
It drives the real ASGI app over httpx, which awaits the app directly and so
keeps both requests on the test's own event loop — the property under test.
TestClient is avoided because how it maps requests onto loops depends on
whether it is used as a context manager, which is too subtle to rest on here.

The stopwatch starts before the slow request is launched, not once the handler
reports it is blocking. Starting it later would miss the stall entirely: the
coroutine that would read the clock cannot run while the loop is captive, so
it would only get to start timing after the block was already over.

The flip side is that the elapsed time includes executor hand-off and OS
scheduling, so a failure means "something held this up", not specifically "the
call moved back onto the loop". The margin below is wide enough that ordinary
scheduling noise does not reach it.

Both scenarios reproduce a stall that was real before the to_thread migration:
a several-second repo call, and an Ollama probe that hangs for up to 15s.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from types import SimpleNamespace
from typing import Any, Iterator

import httpx
import pytest

import app

# How long the stubbed blocking work takes. Long enough that a stalled loop is
# unambiguous, short enough not to slow the suite down.
_BLOCK_SECONDS = 1.0
# A healthy loop answers in milliseconds; half the block is a wide margin that
# still fails loudly if the call moves back onto the loop.
_MAX_HEALTH_SECONDS = _BLOCK_SECONDS / 2
_TIMEOUT = 10.0


@pytest.fixture(autouse=True)
def stub_health_db(monkeypatch: pytest.MonkeyPatch):
  """/api/health only proves the loop is alive here, so keep it off the DB."""

  @contextlib.contextmanager
  def fake_connection() -> Iterator[Any]:
    yield SimpleNamespace(execute=lambda *a, **kw: SimpleNamespace(fetchone=lambda: (1,)))

  monkeypatch.setattr(app.db, "connection", fake_connection)


@contextlib.asynccontextmanager
async def _client() -> Any:
  transport = httpx.ASGITransport(app=app.app)
  async with httpx.AsyncClient(
    transport=transport, base_url="http://test", timeout=_TIMEOUT
  ) as client:
    yield client


async def _health_latency_during(client: httpx.AsyncClient, slow: Any, started: threading.Event):
  """Elapsed seconds from launching `slow` to having /api/health answered."""
  t0 = time.monotonic()
  task = asyncio.create_task(slow)
  # Yield until the handler reports it reached the blocking call, so the health
  # request cannot pass by simply arriving too early. If the loop is captive we
  # only get back here once the stall ends — which the elapsed check then flags.
  while not started.is_set() and time.monotonic() - t0 < _TIMEOUT:
    await asyncio.sleep(0.005)
  assert started.is_set(), "the handler never reached its blocking call"

  resp = await client.get("/api/health")
  elapsed = time.monotonic() - t0
  await task
  return resp, elapsed


class TestSlowRepoCall:
  """DELETE /api/feeds wipes the database; it is the longest repo call there is."""

  def test_health_answers_while_a_delete_is_running(self, monkeypatch: pytest.MonkeyPatch) -> None:
    started = threading.Event()

    def blocking_delete() -> None:
      started.set()
      time.sleep(_BLOCK_SECONDS)

    monkeypatch.setattr(app.repo, "delete_all_data", blocking_delete)
    monkeypatch.setattr(app.cache, "purge_feed_cache", lambda: 0)

    async def scenario() -> None:
      monkeypatch.setattr(app, "_refresh_lock", asyncio.Lock())
      async with _client() as client:
        resp, elapsed = await _health_latency_during(
          client, client.delete("/api/feeds"), started
        )
        assert resp.status_code == 200
        assert elapsed < _MAX_HEALTH_SECONDS, (
          f"/api/health took {elapsed:.2f}s while repo.delete_all_data was running "
          f"— the call is back on the event loop (or the machine is badly overloaded)"
        )

    asyncio.run(scenario())


class TestSlowLlmProbe:
  """The worst case before the migration: an unreachable Ollama froze the app
  for the full probe timeout (5s) times _LLM_PROBE_ATTEMPTS."""

  def test_health_answers_while_the_llm_probe_hangs(self, monkeypatch: pytest.MonkeyPatch) -> None:
    started = threading.Event()

    def hanging_probe(base_url: str) -> tuple[bool, str]:
      started.set()
      time.sleep(_BLOCK_SECONDS)
      return False, "timed out"

    feed = SimpleNamespace(extraction_rules={"item": "article"}, url="https://example.com")
    monkeypatch.setattr(app, "_get_feed_or_404", lambda feed_id: feed)
    monkeypatch.setattr(app, "_probe_llm", hanging_probe)
    # raising=False: lifespan never runs here, so state.config does not exist
    # yet. monkeypatch removes it again afterwards.
    monkeypatch.setattr(
      app.app.state,
      "config",
      SimpleNamespace(ollama=SimpleNamespace(enabled=True, base_url="http://ollama.invalid:11434")),
      raising=False,
    )

    async def scenario() -> None:
      async with _client() as client:
        resp, elapsed = await _health_latency_during(
          client, client.post("/api/feeds/1/rules/infer"), started
        )
        # The probe reports failure, so the infer request 503s — after the loop
        # has already served health.
        assert resp.status_code == 200
        assert elapsed < _MAX_HEALTH_SECONDS, (
          f"/api/health took {elapsed:.2f}s while _probe_llm was hanging "
          f"— the probe is back on the event loop (or the machine is badly overloaded)"
        )

    asyncio.run(scenario())
