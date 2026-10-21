"""Runs the real app lifespan with its external effects stubbed out.

The DB, the config file and the background summarizer are replaced, so this
checks only what lifespan wires up before the first request is served.
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
