"""Behavioral test for GET /api/articles ordering.

Locks the wiring that `app.state.config.article_order` actually reaches
`repo.list_articles(order=...)`: a unit test on the repo alone cannot catch the
endpoint passing a constant or dropping the argument.

The TestClient is built without the lifespan context manager so no background
tasks or DB probe run; `app.state.config` is set directly and the DB is provided
by the `clean_db` fixture.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import app as app_module
import db
import repo
from models import Article
from schemas import AppConfig

pytestmark = pytest.mark.usefixtures("clean_db")


def _seed_two_articles() -> None:
  with db.connection() as conn:
    conn.execute(
      """INSERT INTO feeds (name, url, translate, summarize, enabled)
         VALUES ('F', 'https://f.example.com/rss', FALSE, TRUE, TRUE)
         RETURNING id"""
    ).fetchone()
  repo.upsert_articles(
    [
      Article(
        title="old",
        url="u-old",
        source="F",
        published=datetime(2026, 5, 20, 8, 0, 0, tzinfo=timezone.utc),
        content_snippet="body",
      ),
      Article(
        title="new",
        url="u-new",
        source="F",
        published=datetime(2026, 5, 21, 8, 0, 0, tzinfo=timezone.utc),
        content_snippet="body",
      ),
    ],
    {"F": 1},
  )


def _urls(config: AppConfig) -> list[str]:
  app_module.app.state.config = config
  client = TestClient(app_module.app)
  resp = client.get("/api/articles")
  assert resp.status_code == 200
  return [a["url"] for a in resp.json()]


def test_endpoint_oldest_first_by_default() -> None:
  _seed_two_articles()
  assert _urls(AppConfig(article_order="oldest")) == ["u-old", "u-new"]


def test_endpoint_newest_first_when_configured() -> None:
  _seed_two_articles()
  assert _urls(AppConfig(article_order="newest")) == ["u-new", "u-old"]
