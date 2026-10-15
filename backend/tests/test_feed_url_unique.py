"""One feed per URL: repo.add_feed and POST /api/feeds reject a duplicate.

The unique index (db migration 3) is what enforces it, so these run against
the real test database. The OPML side lives in test_opml_endpoint.py.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

import app as app_module
import repo
from schemas import AppConfig, FeedRow

pytestmark = pytest.mark.usefixtures("clean_db")

URL = "https://a.example.com/feed"


def test_add_feed_rejects_a_duplicate_url():
  repo.add_feed(FeedRow(name="first", url=URL))
  with pytest.raises(repo.DuplicateFeedError):
    repo.add_feed(FeedRow(name="second", url=URL))
  assert [f.name for f in repo.list_feeds()] == ["first"]


def test_add_feed_allows_several_feeds_without_url():
  repo.add_feed(FeedRow(name="a", url=None))
  repo.add_feed(FeedRow(name="b", url=None))
  assert len(repo.list_feeds()) == 2


def test_post_feed_with_an_existing_url_is_a_conflict(monkeypatch: pytest.MonkeyPatch):
  async def fake_probe(*_args: Any, **_kwargs: Any) -> app_module.ProbeResult:
    return app_module.ProbeResult(title="Probed")

  monkeypatch.setattr(app_module, "_probe_feed", fake_probe)
  app_module.app.state.config = AppConfig()
  repo.add_feed(FeedRow(name="first", url=URL))

  resp = TestClient(app_module.app).post("/api/feeds", json={"name": "", "url": URL})

  assert resp.status_code == 409
  assert URL in resp.json()["detail"]
  assert [f.name for f in repo.list_feeds()] == ["first"]
