"""Guards for what the async handlers in app.py promise under concurrency.

Moving their blocking calls onto worker threads introduced await points where
there were none. Two things follow from that, and both fail silently:

  - cancelling an await does not stop the worker it started, so anything that
    must outlive the request has to be wrapped in asyncio.shield
  - the event loop is no longer held for the whole of a read-modify-write, so
    the exclusion that used to come for free now needs a real lock

The invariants pinned here:

  - a refresh_jobs row that was created always gets a task that will finish it.
    Otherwise /api/status reports running=true forever, with no way back short
    of a restart.
  - _refresh_lock stays held until the full delete finishes. Otherwise a
    concurrent refresh can start repopulating the database mid-delete.
  - _opml_import_lock serializes imports, and keeps doing so when the request
    that took it is cancelled.

Cancellation reaches these handlers on server shutdown, which is a routine
operation here (`docker compose restart web`).

No DB and no real refresh: the blocking calls are monkeypatched.
"""

from __future__ import annotations

import asyncio
import io
import threading
import time
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import UploadFile

import app

_TIMEOUT = 5.0

# Sentinel config: every path that would read it is stubbed out.
_CONFIG = SimpleNamespace()
# OPML parsing is left real (it is pure), so it needs the one field it reads.
_OPML_CONFIG = SimpleNamespace(native_lang="en")

_OPML = """<?xml version="1.0"?>
<opml version="2.0"><body>
  <outline type="rss" text="F1" xmlUrl="https://a.example.com/feed"/>
</body></opml>"""


def _request(config: Any = _CONFIG) -> Any:
  return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=config)))


def _upload() -> UploadFile:
  return UploadFile(file=io.BytesIO(_OPML.encode("utf-8")), filename="subs.opml")


class _PeakTracker:
  """Records how many workers were inside the guarded section at once."""

  def __init__(self) -> None:
    self._lock = threading.Lock()
    self._inside = 0
    self.peak = 0

  def __enter__(self) -> None:
    with self._lock:
      self._inside += 1
      self.peak = max(self.peak, self._inside)

  def __exit__(self, *exc_info: object) -> None:
    with self._lock:
      self._inside -= 1


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


class TestOpmlImportExclusion:
  """Two imports must not interleave.

  _persist_opml_feeds reads the existing feed/folder sets before writing them.
  On the event loop that read-modify-write was exclusive by accident; in a
  worker thread it is not, and feeds.url has no UNIQUE constraint to catch the
  double registration that follows.
  """

  def test_a_second_import_waits_for_the_first(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    started, release = threading.Event(), threading.Event()
    tracker = _PeakTracker()

    def blocking_persist(folders: Any, uncategorized: Any) -> tuple[int, int, int]:
      with tracker:
        started.set()
        release.wait(_TIMEOUT)
      return 0, 0, 0

    monkeypatch.setattr(app, "_persist_opml_feeds", blocking_persist)

    async def scenario() -> None:
      monkeypatch.setattr(app, "_opml_import_lock", asyncio.Lock())
      first = asyncio.create_task(app.opml_import(_upload(), _request(_OPML_CONFIG)))
      assert await asyncio.to_thread(started.wait, _TIMEOUT)

      second = asyncio.create_task(app.opml_import(_upload(), _request(_OPML_CONFIG)))
      # Long enough that an unguarded second import would reach its worker.
      await asyncio.sleep(0.05)
      assert tracker.peak == 1, "a second import ran while the first was writing"

      release.set()
      await first
      await second
      assert tracker.peak == 1

    asyncio.run(scenario())

  def test_cancelling_an_import_keeps_the_lock_until_the_worker_finishes(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """Same shape as the delete handler: the lock must outlive the request."""
    started, release = threading.Event(), threading.Event()

    def blocking_persist(folders: Any, uncategorized: Any) -> tuple[int, int, int]:
      started.set()
      release.wait(_TIMEOUT)
      return 0, 0, 0

    monkeypatch.setattr(app, "_persist_opml_feeds", blocking_persist)

    async def scenario() -> None:
      lock = asyncio.Lock()
      monkeypatch.setattr(app, "_opml_import_lock", lock)
      task = asyncio.create_task(app.opml_import(_upload(), _request(_OPML_CONFIG)))
      assert await asyncio.to_thread(started.wait, _TIMEOUT)

      task.cancel()
      with pytest.raises(asyncio.CancelledError):
        await task

      assert lock.locked(), "lock was released while the import was still writing"
      release.set()
      assert await _wait_until(lambda: not lock.locked())

    asyncio.run(scenario())
