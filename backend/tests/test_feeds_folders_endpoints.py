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

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as app_module
import cache
import db
import repo
from schemas import AppConfig, FeedRow

pytestmark = pytest.mark.usefixtures("clean_db")


def _client() -> TestClient:
  return TestClient(app_module.app)


def _make_feed(name: str, folder_id: int | None = None, *, summarize: bool = True) -> int:
  with db.connection() as conn:
    row = conn.execute(
      """INSERT INTO feeds (name, url, translate, summarize, enabled, folder_id)
         VALUES (%s, %s, FALSE, %s, TRUE, %s) RETURNING id""",
      (name, f"https://{name}.example.com/rss", summarize, folder_id),
    ).fetchone()
    assert row is not None
    return int(row["id"])


def _feed_names() -> list[str]:
  return [f.name for f in repo.list_feeds()]


def _folder_names() -> list[str]:
  return [f.name for f in repo.list_folders()]


def _row_count(table: str) -> int:
  with db.connection() as conn:
    row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
    assert row is not None
    return int(row["n"])


@pytest.fixture
def cache_dir(tmp_path: Path) -> Iterator[Path]:
  """Point the feed cache at a tmp dir. DELETE /api/feeds purges it, and the
  default location is the repo's cache/ — the live one inside the container."""
  original_cache = cache._cache_dir
  original_feed = cache._feed_cache_dir
  cache.configure(tmp_path)
  yield tmp_path
  cache._cache_dir = original_cache
  cache._feed_cache_dir = original_feed


class TestReorder:
  def test_feed_reorder_body_reaches_the_repo(self):
    ids = [_make_feed(name) for name in ("A", "B", "C")]
    assert _feed_names() == ["A", "B", "C"]

    res = _client().put("/api/feeds/reorder", json={"feed_ids": [ids[2], ids[0], ids[1]]})

    assert res.status_code == 204
    assert _feed_names() == ["C", "A", "B"]

  def test_folder_reorder_body_reaches_the_repo(self):
    ids = [repo.create_folder(name).id for name in ("A", "B", "C")]
    assert _folder_names() == ["A", "B", "C"]

    res = _client().put("/api/folders/reorder", json={"folder_ids": [ids[2], ids[0], ids[1]]})

    assert res.status_code == 204
    assert _folder_names() == ["C", "A", "B"]


class TestDeleteFeed:
  def test_deletes_only_the_requested_feed(self):
    keep = _make_feed("keep")
    doomed = _make_feed("doomed")

    res = _client().delete(f"/api/feeds/{doomed}")

    assert res.status_code == 204
    assert [f.id for f in repo.list_feeds()] == [keep]


class TestDeleteFolder:
  def test_deletes_only_the_requested_folder(self):
    keep = repo.create_folder("keep")
    doomed = repo.create_folder("doomed")

    res = _client().delete(f"/api/folders/{doomed.id}")

    assert res.status_code == 204
    assert [f.id for f in repo.list_folders()] == [keep.id]

  def test_feeds_in_the_folder_survive_unfiled(self):
    folder = repo.create_folder("Tech")
    feed_id = _make_feed("A", folder.id)

    _client().delete(f"/api/folders/{folder.id}")

    feed = repo.get_feed_by_id(feed_id)
    assert feed is not None
    assert feed.folder_id is None


class TestRenameFolder:
  def test_renames_and_returns_the_folder(self):
    folder = repo.create_folder("Before")

    res = _client().patch(f"/api/folders/{folder.id}", json={"name": "After"})

    assert res.status_code == 200
    assert res.json()["id"] == folder.id
    assert res.json()["name"] == "After"
    assert _folder_names() == ["After"]

  def test_unknown_folder_is_404(self):
    assert _client().patch("/api/folders/9999", json={"name": "X"}).status_code == 404

  @pytest.mark.parametrize("name", ["", "   "])
  def test_blank_name_is_422_and_keeps_the_old_name(self, name: str):
    folder = repo.create_folder("Before")

    res = _client().patch(f"/api/folders/{folder.id}", json={"name": name})

    assert res.status_code == 422
    assert _folder_names() == ["Before"]

  def test_name_is_trimmed(self):
    folder = repo.create_folder("Before")

    res = _client().patch(f"/api/folders/{folder.id}", json={"name": "  After  "})

    assert res.json()["name"] == "After"
    assert _folder_names() == ["After"]


class TestCreateFolder:
  def test_creates_with_a_trimmed_name(self):
    res = _client().post("/api/folders", json={"name": "  Tech  "})

    assert res.status_code == 201
    assert res.json()["name"] == "Tech"
    assert _folder_names() == ["Tech"]

  @pytest.mark.parametrize("name", ["", "   "])
  def test_blank_name_is_422(self, name: str):
    res = _client().post("/api/folders", json={"name": name})

    assert res.status_code == 422
    assert _folder_names() == []


class TestFetchFeed:
  def test_unknown_feed_is_404(self):
    assert _client().post("/api/feeds/9999/fetch").status_code == 404

  def test_starts_a_fetch_of_that_feed(self, monkeypatch: pytest.MonkeyPatch):
    _make_feed("other")
    feed_id = _make_feed("A")
    fetched: list[int] = []

    # Record from the coroutine body, so the test also fails when the fetch is
    # created but never scheduled.
    async def record_fetch(feed: FeedRow, _config: AppConfig) -> None:
      fetched.append(feed.id)

    monkeypatch.setattr(app_module, "_fetch_single_feed", record_fetch)
    monkeypatch.setattr(app_module.app.state, "config", AppConfig(), raising=False)

    res = _client().post(f"/api/feeds/{feed_id}/fetch")

    assert res.status_code == 202
    assert fetched == [feed_id]


class TestDeleteAllData:
  def _seed(self, cache_dir: Path) -> None:
    folder = repo.create_folder("Tech")
    feed_id = _make_feed("A", folder.id)
    with db.connection() as conn:
      conn.execute(
        "INSERT INTO articles (url, title, source, feed_id) VALUES (%s, %s, %s, %s)",
        ("https://a.example.com/1", "Title", "A", feed_id),
      )
      conn.execute("INSERT INTO refresh_jobs (source) VALUES ('manual')")
      conn.execute("INSERT INTO filters (pattern) VALUES ('spam')")
    cache.save_feed_cache("https://A.example.com/rss", None, None, "<rss/>")
    assert any((cache_dir / "feeds").iterdir())

  def test_wipes_feeds_articles_folders_and_jobs(self, cache_dir: Path):
    self._seed(cache_dir)

    res = _client().delete("/api/feeds")

    assert res.status_code == 204
    for table in ("feeds", "articles", "folders", "refresh_jobs"):
      assert _row_count(table) == 0, table

  def test_keeps_filters(self, cache_dir: Path):
    # Pins current behavior: the confirm dialog promises to delete feeds,
    # folders, and articles only, so NG-word filters outlive a wipe.
    self._seed(cache_dir)

    _client().delete("/api/feeds")

    assert _row_count("filters") == 1

  def test_purges_the_feed_cache(self, cache_dir: Path):
    self._seed(cache_dir)

    _client().delete("/api/feeds")

    assert list((cache_dir / "feeds").iterdir()) == []


class TestFeedUpdates:
  """Each handler answers with the post-update row; the frontend caches that
  body, so a stale or wrong response is as bad as a failed write."""

  def test_move_to_folder(self):
    folder = repo.create_folder("Tech")
    feed_id = _make_feed("A")

    res = _client().patch(f"/api/feeds/{feed_id}/folder", json={"folder_id": folder.id})

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

  def test_toggle_summarize_flips_each_time(self):
    # Two requests: a handler that always writes the same value passes one.
    feed_id = _make_feed("A", summarize=False)

    first = _client().post(f"/api/feeds/{feed_id}/summarize")
    second = _client().post(f"/api/feeds/{feed_id}/summarize")

    assert (first.status_code, first.json()["summarize"]) == (200, True)
    assert (second.status_code, second.json()["summarize"]) == (200, False)
    feed = repo.get_feed_by_id(feed_id)
    assert feed is not None
    assert feed.summarize is False

  def test_toggle_summarize_unknown_feed_is_404(self):
    assert _client().post("/api/feeds/9999/summarize").status_code == 404

  # Both directions: one alone would pass a handler that ignores the body
  # and always writes the same value.
  @pytest.mark.parametrize("translate", [True, False])
  def test_translate(self, translate: bool):
    feed_id = _make_feed("A")
    repo.update_feed_translate(feed_id, not translate)

    res = _client().patch(f"/api/feeds/{feed_id}/translate", json={"translate": translate})

    assert res.status_code == 200
    assert res.json()["translate"] is translate
    feed = repo.get_feed_by_id(feed_id)
    assert feed is not None
    assert feed.translate is translate

  def test_translate_unknown_feed_is_404(self):
    res = _client().patch("/api/feeds/9999/translate", json={"translate": True})
    assert res.status_code == 404

  def test_site_url_is_stored_stripped(self):
    feed_id = _make_feed("A")

    res = _client().patch(
      f"/api/feeds/{feed_id}/site_url", json={"site_url": "  https://a.example.com/  "}
    )

    assert res.status_code == 200
    assert res.json()["site_url"] == "https://a.example.com/"
    feed = repo.get_feed_by_id(feed_id)
    assert feed is not None
    assert feed.site_url == "https://a.example.com/"

  @pytest.mark.parametrize("cleared", ["   ", None])
  def test_blank_or_null_site_url_clears_it(self, cleared: str | None):
    feed_id = _make_feed("A")
    repo.update_feed_site_url(feed_id, "https://a.example.com/")

    res = _client().patch(f"/api/feeds/{feed_id}/site_url", json={"site_url": cleared})

    assert res.status_code == 200
    assert res.json()["site_url"] is None
    feed = repo.get_feed_by_id(feed_id)
    assert feed is not None
    assert feed.site_url is None

  def test_site_url_unknown_feed_is_404(self):
    res = _client().patch("/api/feeds/9999/site_url", json={"site_url": "https://x.example/"})
    assert res.status_code == 404
