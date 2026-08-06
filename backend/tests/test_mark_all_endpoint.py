"""Behavioral tests for the bulk read-state endpoints and their scope params.

Locks the wiring that `feed_id` / `folder_id` query params actually reach
`repo.mark_all_read` / `repo.mark_all_unread`: a repo-level unit test cannot
catch the endpoint dropping a scope argument and falling back to a global
update, which is exactly how the folder scope was silently global before.

The TestClient is built without the lifespan context manager so no background
tasks or DB probe run; the DB is provided by the `clean_db` fixture.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import app as app_module
import db
import repo
from models import Article

pytestmark = pytest.mark.usefixtures("clean_db")


def _make_feed(name: str, folder_id: int | None = None) -> int:
  with db.connection() as conn:
    row = conn.execute(
      """INSERT INTO feeds (name, url, translate, summarize, enabled, folder_id)
         VALUES (%s, %s, FALSE, TRUE, TRUE, %s) RETURNING id""",
      (name, f"https://{name}.example.com/rss", folder_id),
    ).fetchone()
    assert row is not None
    return int(row["id"])


def _seed_two_feeds_one_foldered() -> tuple[int, dict[str, int]]:
  """Feed A lives in a folder, feed B does not. One article each."""
  folder = repo.create_folder("Tech")
  ids = {"A": _make_feed("A", folder.id), "B": _make_feed("B")}
  for source in ("A", "B"):
    repo.upsert_articles(
      [
        Article(
          title=source,
          url=f"u-{source}",
          source=source,
          published=datetime(2026, 5, 20, 8, 0, 0, tzinfo=timezone.utc),
          content_snippet="body",
        )
      ],
      {source: ids[source]},
    )
  return folder.id, ids


def _read_state() -> dict[str, bool]:
  return {row.url: row.read_at is not None for row in repo.list_articles()}


def test_read_all_scopes_to_folder() -> None:
  folder_id, _ = _seed_two_feeds_one_foldered()
  client = TestClient(app_module.app)
  resp = client.post(f"/api/articles/read-all?folder_id={folder_id}")
  assert resp.status_code == 200
  assert resp.json() == {"marked": 1}
  assert _read_state() == {"u-A": True, "u-B": False}


def test_read_all_scopes_to_feed() -> None:
  _, ids = _seed_two_feeds_one_foldered()
  client = TestClient(app_module.app)
  resp = client.post(f"/api/articles/read-all?feed_id={ids['B']}")
  assert resp.status_code == 200
  assert resp.json() == {"marked": 1}
  assert _read_state() == {"u-A": False, "u-B": True}


def test_read_all_without_scope_is_global() -> None:
  _seed_two_feeds_one_foldered()
  client = TestClient(app_module.app)
  resp = client.post("/api/articles/read-all")
  assert resp.status_code == 200
  assert resp.json() == {"marked": 2}
  assert _read_state() == {"u-A": True, "u-B": True}


def test_unread_all_scopes_to_folder() -> None:
  folder_id, _ = _seed_two_feeds_one_foldered()
  repo.mark_read(["u-A", "u-B"])
  client = TestClient(app_module.app)
  resp = client.post(f"/api/articles/unread-all?folder_id={folder_id}")
  assert resp.status_code == 200
  assert resp.json() == {"unmarked": 1}
  assert _read_state() == {"u-A": False, "u-B": True}


def test_unknown_folder_marks_nothing() -> None:
  _seed_two_feeds_one_foldered()
  client = TestClient(app_module.app)
  resp = client.post("/api/articles/read-all?folder_id=999999")
  assert resp.status_code == 200
  assert resp.json() == {"marked": 0}
  assert _read_state() == {"u-A": False, "u-B": False}
