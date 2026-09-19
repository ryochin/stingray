"""Guards for cancellation safety around the refresh lifecycle in app.py.

Moving the refresh_jobs INSERT and the delete-everything handler onto worker
threads introduced await points where there were none, and cancelling an await
does not stop the worker that is already running. Two invariants now rest on
asyncio.shield, and both break silently if the shield is dropped:

  - a refresh_jobs row that was created always gets a task that will finish it.
    Otherwise /api/status reports running=true forever, with no way back short
    of a restart.
  - _refresh_lock stays held until the full delete finishes. Otherwise a
    concurrent refresh can start repopulating the database mid-delete.

Cancellation reaches these handlers on server shutdown, which is a routine
operation here (`docker compose restart web`).

No DB and no real refresh: the blocking calls are monkeypatched.
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace
from typing import Any, Callable

import pytest

import app

_TIMEOUT = 5.0

# Sentinel config: every path that would read it is stubbed out.
_CONFIG = SimpleNamespace()


def _request() -> Any:
  return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=_CONFIG)))


async def _wait_until(predicate: Callable[[], bool]) -> bool:
  """Poll until predicate holds, yielding to the loop so shielded work runs."""
  deadline = time.monotonic() + _TIMEOUT
  while time.monotonic() < deadline:
    if predicate():
      return True
    await asyncio.sleep(0.01)
  return False


@pytest.fixture(autouse=True)
def clean_refresh_state():
  """No refresh in flight before or after each test."""
  app._refresh_task = None
  app._background_tasks.clear()
  yield
  app._refresh_task = None
  app._background_tasks.clear()


class TestRefreshStart:
  def test_cancelling_the_request_still_spawns_the_refresh_task(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """A job row created in a worker thread must not be orphaned.

    Without the shield the INSERT still lands but create_task() never runs, so
    refresh_jobs keeps a 'running' row that nothing will ever finish.
    """
    started, release = threading.Event(), threading.Event()

    def blocking_create(source: str) -> int:
      started.set()
      release.wait(_TIMEOUT)
      return 42

    spawned: list[int | None] = []

    async def fake_run_refresh(
      config: Any, *, trigger: str = "web", job_id: int | None = None, force: bool = False
    ) -> None:
      spawned.append(job_id)

    monkeypatch.setattr(app.repo, "create_refresh_job", blocking_create)
    monkeypatch.setattr(app, "_run_refresh", fake_run_refresh)

    async def scenario() -> None:
      monkeypatch.setattr(app, "_refresh_lock", asyncio.Lock())
      task = asyncio.create_task(app.trigger_refresh(_request()))
      assert await asyncio.to_thread(started.wait, _TIMEOUT)

      task.cancel()
      with pytest.raises(asyncio.CancelledError):
        await task

      release.set()
      assert await _wait_until(lambda: app._refresh_task is not None)
      refresh_task = app._refresh_task
      assert refresh_task is not None
      await refresh_task
      assert spawned == [42]

    asyncio.run(scenario())


class TestDeleteAllData:
  def test_cancelling_the_request_keeps_the_lock_until_the_delete_finishes(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """The lock must outlive the request, not the `async with` block.

    Shielding the awaits alone would not be enough — the `async with` would
    still unwind on cancellation and free the lock while the worker deletes.
    """
    started, release = threading.Event(), threading.Event()

    def blocking_delete() -> None:
      started.set()
      release.wait(_TIMEOUT)

    monkeypatch.setattr(app.repo, "delete_all_data", blocking_delete)
    monkeypatch.setattr(app.cache, "purge_feed_cache", lambda: 0)

    async def scenario() -> None:
      lock = asyncio.Lock()
      monkeypatch.setattr(app, "_refresh_lock", lock)
      task = asyncio.create_task(app.delete_all_data())
      assert await asyncio.to_thread(started.wait, _TIMEOUT)

      task.cancel()
      with pytest.raises(asyncio.CancelledError):
        await task

      assert lock.locked(), "lock was released while the delete was still running"
      release.set()
      assert await _wait_until(lambda: not lock.locked())

    asyncio.run(scenario())
