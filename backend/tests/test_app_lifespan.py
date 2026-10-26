"""Runs the real app lifespan with its external effects stubbed out.

The DB, the config file and the background summarizer are replaced, so this
checks only what lifespan wires up before the first request is served and
what it tears down after the last one.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI

import app as app_module
import cache
import db
from schemas import AppConfig


@pytest.fixture
def stubbed_lifespan(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
  cache_dir: Path = tmp_path / "custom-cache"
  config: AppConfig = AppConfig(cache_dir=str(cache_dir))

  async def idle_summarizer(_config: AppConfig) -> None:
    await asyncio.Event().wait()

  monkeypatch.setattr(app_module.AppConfig, "load", lambda: config)
  monkeypatch.setattr(db, "configure", lambda: None)
  monkeypatch.setattr(db, "init_schema", lambda: None)
  monkeypatch.setattr(db, "close", lambda: None)
  # Lifespan registers the summarizer here; start every test from an empty set.
  monkeypatch.setattr(app_module, "_background_tasks", set())
  monkeypatch.setattr(app_module, "_background_summarizer", idle_summarizer)
  # The module default is the repo's cache/, the live one inside the
  # container. Point it at tmp so a lifespan that skips configure() writes
  # there instead; monkeypatch also restores whatever lifespan sets.
  monkeypatch.setattr(cache, "_cache_dir", tmp_path / "default-cache")
  monkeypatch.setattr(cache, "_feed_cache_dir", tmp_path / "default-cache" / "feeds")
  return cache_dir


def test_lifespan_points_the_feed_cache_at_config_cache_dir(stubbed_lifespan: Path):
  async def run() -> None:
    # A throwaway app, so lifespan's app.state.config does not leak into the
    # shared one other tests use.
    async with app_module.lifespan(FastAPI()):
      cache.save_feed_cache("https://example.com/feed.xml", None, None, "<rss/>")

  asyncio.run(run())

  assert len(list((stubbed_lifespan / "feeds").iterdir())) == 1


class TestShutdown:
  def test_background_tasks_finish_their_cleanup_before_the_pool_closes(
    self, stubbed_lifespan: Path, monkeypatch: pytest.MonkeyPatch
  ):
    events: list[str] = []
    monkeypatch.setattr(db, "close", lambda: events.append("db.close"))

    async def task_with_cleanup(started: asyncio.Event) -> None:
      started.set()
      try:
        await asyncio.Event().wait()
      except asyncio.CancelledError:
        # Cleanup that itself awaits, as closing a refresh_jobs row does.
        await asyncio.sleep(0)
        events.append("cleanup")
        raise

    async def run() -> None:
      async with app_module.lifespan(FastAPI()):
        started = asyncio.Event()
        app_module._spawn_background(task_with_cleanup(started))
        await started.wait()

    asyncio.run(run())

    assert events == ["cleanup", "db.close"]

  def test_a_task_ignoring_cancellation_does_not_block_shutdown(
    self, stubbed_lifespan: Path, monkeypatch: pytest.MonkeyPatch
  ):
    closed: list[bool] = []
    monkeypatch.setattr(db, "close", lambda: closed.append(True))
    monkeypatch.setattr(app_module, "_SHUTDOWN_TIMEOUT_S", 0.05)

    async def run() -> None:
      release = asyncio.Event()
      tasks: list[asyncio.Task[object]] = []

      async def stubborn(started: asyncio.Event) -> None:
        started.set()
        while not release.is_set():
          try:
            await release.wait()
          except asyncio.CancelledError:
            continue

      async def serve_and_shut_down() -> None:
        async with app_module.lifespan(FastAPI()):
          started = asyncio.Event()
          tasks.append(app_module._spawn_background(stubborn(started)))
          await started.wait()
        assert closed == [True]
        assert not tasks[0].done()

      try:
        # A shutdown that ignored its timeout would wait on the task forever.
        await asyncio.wait_for(serve_and_shut_down(), timeout=2)
      finally:
        release.set()
        await asyncio.gather(*tasks)

    asyncio.run(run())


class _StopLoop(Exception):
  """Raised by the fake sleep to break out of the summarizer's endless loop."""


def test_background_summarizer_keeps_running_after_an_error(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  results: list[Exception | int] = [RuntimeError("ollama down"), 0]
  summarize_calls: list[AppConfig] = []
  sleeps: list[float] = []

  async def fake_summarize_pending(config: AppConfig) -> int:
    summarize_calls.append(config)
    result = results.pop(0)
    if isinstance(result, Exception):
      raise result
    return result

  async def fake_sleep(delay: float) -> None:
    sleeps.append(delay)
    # Initial delay, then one sleep after each of the two passes.
    if len(sleeps) == 3:
      raise _StopLoop

  monkeypatch.setattr(app_module, "summarize_pending", fake_summarize_pending)
  monkeypatch.setattr(app_module, "_summarize_lock", asyncio.Lock())
  monkeypatch.setattr(app_module.asyncio, "sleep", fake_sleep)
  config = AppConfig()

  with pytest.raises(_StopLoop):
    asyncio.run(app_module._background_summarizer(config))

  assert summarize_calls == [config, config]
  assert sleeps == [10, 60, 60]
