"""_run_refresh closes its refresh_jobs row when cancelled.

The row is created by _start_refresh before _run_refresh starts, and
refresh_all only closes it on success or an Exception. Cancellation — server
shutdown or the delete-all endpoint — would otherwise leave it 'running', and
/api/status would report a refresh that is not happening. refresh_all is
stubbed; the job row lives in the test DB.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

import app as app_module
import repo
from schemas import AppConfig

pytestmark = pytest.mark.usefixtures("clean_db")


@pytest.fixture(autouse=True)
def fresh_locks(monkeypatch: pytest.MonkeyPatch) -> None:
  """A contended asyncio.Lock binds to the loop it waits on; each test runs
  in a loop of its own, so it gets a lock of its own."""
  monkeypatch.setattr(app_module, "_summarize_lock", asyncio.Lock())


def _job_status(job_id: int) -> tuple[str, str | None]:
  job = repo.get_latest_refresh_job()
  assert job is not None and job.id == job_id
  return job.status, job.error


class TestRunRefreshCancellation:
  def test_cancelled_while_waiting_for_the_summarizer_lock(self, monkeypatch: pytest.MonkeyPatch):
    calls: list[Any] = []

    async def refresh_all(*args: Any, **kwargs: Any) -> None:
      calls.append(args)

    monkeypatch.setattr(app_module, "refresh_all", refresh_all)
    job_id = repo.create_refresh_job("web")

    async def run() -> None:
      async with app_module._summarize_lock:
        task = asyncio.create_task(app_module._run_refresh(AppConfig(), job_id=job_id))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
          await task

    asyncio.run(run())

    assert calls == []
    assert _job_status(job_id) == ("failed", "cancelled")

  def test_cancelled_inside_refresh_all(self, monkeypatch: pytest.MonkeyPatch):
    async def run() -> None:
      started = asyncio.Event()

      async def refresh_all(*args: Any, **kwargs: Any) -> None:
        started.set()
        await asyncio.Event().wait()

      monkeypatch.setattr(app_module, "refresh_all", refresh_all)
      task = asyncio.create_task(app_module._run_refresh(AppConfig(), job_id=job_id))
      await started.wait()
      task.cancel()
      with pytest.raises(asyncio.CancelledError):
        await task

    job_id = repo.create_refresh_job("web")
    asyncio.run(run())

    assert _job_status(job_id) == ("failed", "cancelled")
