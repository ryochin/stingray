"""HTTP-level tests for the feed/folder mutation endpoints.

The repo functions behind these are already covered by test_repo_folders.py.
What is not covered is the handler layer: whether the request body actually
reaches the repo call with the right argument. A dropped argument here is
invisible to repo tests and, for the reorder endpoints, invisible in the UI as
well — the frontend applies the new order optimistically, so a request the
backend silently ignores shows the correct order until the next refetch quietly
puts it back. test_mark_all_endpoint.py exists because that exact gap shipped a
bug once already.

Built without the lifespan context manager so no background task or DB probe
runs; the DB comes from the `clean_db` fixture.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app as app_module
import db
import repo

pytestmark = pytest.mark.usefixtures("clean_db")


def _client() -> TestClient:
  return TestClient(app_module.app)


def _make_feed(name: str, folder_id: int | None = None) -> int:
  with db.connection() as conn:
    row = conn.execute(
      """INSERT INTO feeds (name, url, translate, summarize, enabled, folder_id)
         VALUES (%s, %s, FALSE, TRUE, TRUE, %s) RETURNING id""",
      (name, f"https://{name}.example.com/rss", folder_id),
    ).fetchone()
    assert row is not None
    return int(row["id"])


def _feed_names() -> list[str]:
  return [f.name for f in repo.list_feeds()]


def _folder_names() -> list[str]:
  return [f.name for f in repo.list_folders()]


class TestReorder:
  def test_feed_reorder_body_reaches_the_repo(self):
    ids = [_make_feed(name) for name in ("A", "B", "C")]
    assert _feed_names() == ["A", "B", "C"]

    res = _client().put(
      "/api/feeds/reorder", json={"feed_ids": [ids[2], ids[0], ids[1]]}
    )

    assert res.status_code == 204
    assert _feed_names() == ["C", "A", "B"]

  def test_folder_reorder_body_reaches_the_repo(self):
    ids = [repo.create_folder(name).id for name in ("A", "B", "C")]
    assert _folder_names() == ["A", "B", "C"]

    res = _client().put(
      "/api/folders/reorder", json={"folder_ids": [ids[2], ids[0], ids[1]]}
    )

    assert res.status_code == 204
    assert _folder_names() == ["C", "A", "B"]


class TestDeleteFeed:
  def test_deletes_only_the_requested_feed(self):
    keep = _make_feed("keep")
    doomed = _make_feed("doomed")

    res = _client().delete(f"/api/feeds/{doomed}")

    assert res.status_code == 204
    assert [f.id for f in repo.list_feeds()] == [keep]


class TestFeedUpdates:
  """Each handler answers with the post-update row; the frontend caches that
  body, so a stale or wrong response is as bad as a failed write."""

  def test_move_to_folder(self):
    folder = repo.create_folder("Tech")
    feed_id = _make_feed("A")

    res = _client().patch(
      f"/api/feeds/{feed_id}/folder", json={"folder_id": folder.id}
    )

    assert res.status_code == 200
    assert res.json()["folder_id"] == folder.id

  def test_move_out_of_folder_with_explicit_null(self):
    folder = repo.create_folder("Tech")
    feed_id = _make_feed("A", folder.id)

    res = _client().patch(f"/api/feeds/{feed_id}/folder", json={"folder_id": None})

    assert res.status_code == 200
    assert res.json()["folder_id"] is None

  def test_rename(self):
    feed_id = _make_feed("Before")

    res = _client().patch(f"/api/feeds/{feed_id}/name", json={"name": "After"})

    assert res.status_code == 200
    assert res.json()["name"] == "After"
    assert _feed_names() == ["After"]

  def test_toggle_enabled(self):
    feed_id = _make_feed("A")

    res = _client().post(f"/api/feeds/{feed_id}/toggle")

    assert res.status_code == 200
    assert res.json()["enabled"] is False

  def test_toggle_unknown_feed_is_404(self):
    assert _client().post("/api/feeds/9999/toggle").status_code == 404
