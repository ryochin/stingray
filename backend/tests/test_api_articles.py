"""Wiring tests for the `GET /api/articles` query parameters.

The TestClient is built without the lifespan context manager, so the config
the handler reads is stashed on app.state by hand; the DB is provided by the
`clean_db` fixture.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import app as app_module
import db
import repo
from models import Article
from schemas import AppConfig

pytestmark = pytest.mark.usefixtures("clean_db")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
  monkeypatch.setattr(app_module.app.state, "config", AppConfig(), raising=False)
  return TestClient(app_module.app)


def _seed() -> None:
  with db.connection() as conn:
    row = conn.execute(
      "INSERT INTO feeds (name, url, enabled) VALUES ('F', 'https://f.example.com/rss', TRUE) RETURNING id"
    ).fetchone()
    assert row is not None
  now = datetime.now(tz=UTC)
  repo.upsert_articles([
    Article(title="T", url=url, source="F", published=now - timedelta(hours=h),
            content_snippet="body")
    for url, h in (("unread", 5), ("read", 1))
  ], {"F": int(row["id"])})
  repo.mark_read(["read"])


def test_read_within_hours_keeps_recent_reads_in_unread_mode(client: TestClient):
  _seed()
  resp = client.get("/api/articles?unread=true&read_within_hours=24")
  assert resp.status_code == 200
  assert sorted(a["url"] for a in resp.json()) == ["read", "unread"]


def test_unread_alone_returns_only_unread(client: TestClient):
  _seed()
  resp = client.get("/api/articles?unread=true")
  assert [a["url"] for a in resp.json()] == ["unread"]


@pytest.mark.parametrize("hours", [0, 169])
def test_read_within_hours_out_of_range_is_rejected(client: TestClient, hours: int):
  resp = client.get(f"/api/articles?unread=true&read_within_hours={hours}")
  assert resp.status_code == 422
