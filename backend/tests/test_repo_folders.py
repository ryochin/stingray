"""Integration tests for folder/feed organization: CRUD, reorder, FK behavior."""

from __future__ import annotations

import pytest

import db
import repo
from schemas import FeedRow


pytestmark = pytest.mark.usefixtures("clean_db")


class TestFolderCrud:
  def test_create_assigns_incrementing_position(self):
    a = repo.create_folder("A")
    b = repo.create_folder("B")
    c = repo.create_folder("C")
    assert a.position < b.position < c.position

  def test_list_folders_sorted_by_position(self):
    a = repo.create_folder("A")
    b = repo.create_folder("B")
    rows = repo.list_folders()
    assert [f.id for f in rows] == [a.id, b.id]

  def test_rename(self):
    f = repo.create_folder("Old")
    repo.rename_folder(f.id, "New")
    assert repo.list_folders()[0].name == "New"

  def test_delete_removes_folder(self):
    f = repo.create_folder("A")
    repo.delete_folder(f.id)
    assert repo.list_folders() == []


class TestReorderFolders:
  def test_reorder_reassigns_positions(self):
    a = repo.create_folder("A")
    b = repo.create_folder("B")
    c = repo.create_folder("C")
    repo.reorder_folders([c.id, a.id, b.id])
    rows = repo.list_folders()
    assert [f.id for f in rows] == [c.id, a.id, b.id]

  def test_empty_is_noop(self):
    a = repo.create_folder("A")
    repo.reorder_folders([])
    assert repo.list_folders()[0].id == a.id


class TestMoveFeedToFolder:
  def _add_feed(self, name: str) -> int:
    return repo.add_feed(
      FeedRow(name=name, url=f"https://{name}.example.com/rss")
    ).id

  def test_move_into_folder(self):
    fid = self._add_feed("F1")
    folder = repo.create_folder("Work")
    repo.move_feed_to_folder(fid, folder.id)
    feed = repo.get_feed_by_id(fid)
    assert feed is not None and feed.folder_id == folder.id

  def test_move_out_of_folder(self):
    folder = repo.create_folder("Work")
    fid = self._add_feed("F1")
    repo.move_feed_to_folder(fid, folder.id)
    repo.move_feed_to_folder(fid, None)
    feed = repo.get_feed_by_id(fid)
    assert feed is not None and feed.folder_id is None

  def test_delete_folder_nulls_child_feed_folder_id(self):
    # Schema: feeds.folder_id has ON DELETE SET NULL.
    folder = repo.create_folder("Work")
    fid = self._add_feed("F1")
    repo.move_feed_to_folder(fid, folder.id)
    repo.delete_folder(folder.id)
    feed = repo.get_feed_by_id(fid)
    assert feed is not None and feed.folder_id is None


class TestAddFeedPosition:
  def _add(self, name: str) -> FeedRow:
    return repo.add_feed(FeedRow(name=name, url=f"https://{name}.example.com/rss"))

  def test_new_feed_gets_smaller_position(self):
    a = self._add("A")
    b = self._add("B")
    assert b.position < a.position

  def test_list_feeds_surfaces_newest_first(self):
    a = self._add("A")
    b = self._add("B")
    c = self._add("C")
    assert [f.id for f in repo.list_feeds()] == [c.id, b.id, a.id]

  def test_at_top_false_appends_preserving_order(self):
    # Bulk callers (OPML import) append in input order instead of reversing it.
    a = repo.add_feed(
      FeedRow(name="A", url="https://a.example.com/rss"), at_top=False
    )
    b = repo.add_feed(
      FeedRow(name="B", url="https://b.example.com/rss"), at_top=False
    )
    c = repo.add_feed(
      FeedRow(name="C", url="https://c.example.com/rss"), at_top=False
    )
    assert a.position < b.position < c.position
    assert [f.id for f in repo.list_feeds()] == [a.id, b.id, c.id]


class TestReorderFeeds:
  def _add(self, name: str) -> int:
    return repo.add_feed(
      FeedRow(name=name, url=f"https://{name}.example.com/rss")
    ).id

  def test_reorder_reflects_in_list_feeds(self):
    a = self._add("A")
    b = self._add("B")
    c = self._add("C")
    repo.reorder_feeds([c, a, b])
    rows = repo.list_feeds()
    assert [f.id for f in rows] == [c, a, b]

  def test_empty_is_noop(self):
    # New feeds are inserted at the top, so the natural order is newest-first.
    a = self._add("A")
    b = self._add("B")
    repo.reorder_feeds([])
    assert [f.id for f in repo.list_feeds()] == [b, a]

  def test_partial_reorder_keeps_smallest_base_position(self):
    # Only the listed feeds are repositioned; they should reuse the lowest
    # existing position among the selection so unrelated feeds stay put.
    a = self._add("A")
    b = self._add("B")
    c = self._add("C")
    # Reorder only [c, a] — b's position should remain untouched relative
    # to where it was (its original position).
    original_b_pos = next(f.position for f in repo.list_feeds() if f.id == b)
    repo.reorder_feeds([c, a])
    new_b_pos = next(f.position for f in repo.list_feeds() if f.id == b)
    assert new_b_pos == original_b_pos


class TestFeedToggles:
  def _add(self, name: str, **kw: object) -> FeedRow:
    return repo.add_feed(FeedRow(name=name, url=f"https://{name}.example.com/", **kw))

  def test_toggle_enabled_flips_flag(self):
    f = self._add("F")
    assert f.enabled is True
    repo.toggle_feed(f.id)
    after = repo.get_feed_by_id(f.id)
    assert after is not None and after.enabled is False
    repo.toggle_feed(f.id)
    after2 = repo.get_feed_by_id(f.id)
    assert after2 is not None and after2.enabled is True

  def test_toggle_summarize(self):
    f = self._add("F", summarize=False)
    repo.toggle_summarize(f.id)
    after = repo.get_feed_by_id(f.id)
    assert after is not None and after.summarize is True

  def test_update_translate(self):
    f = self._add("F")
    repo.update_feed_translate(f.id, True)
    after = repo.get_feed_by_id(f.id)
    assert after is not None and after.translate is True

  def test_list_feeds_enabled_filter(self):
    a = self._add("A")
    b = self._add("B")
    repo.toggle_feed(b.id)  # disable B
    enabled = repo.list_feeds(enabled=True)
    assert [f.id for f in enabled] == [a.id]
    disabled = repo.list_feeds(enabled=False)
    assert [f.id for f in disabled] == [b.id]


class TestDeleteFeedCascade:
  def test_delete_feed_cascades_to_articles(self):
    # Schema: articles.feed_id has ON DELETE CASCADE.
    fid = repo.add_feed(FeedRow(name="F", url="https://f.example.com/")).id
    with db.connection() as conn:
      conn.execute(
        "INSERT INTO articles (url, feed_id, title, source) VALUES (%s, %s, 'T', 'F')",
        ("https://f.example.com/a1", fid),
      )
    assert len(repo.list_articles()) == 1
    repo.delete_feed(fid)
    assert repo.list_articles() == []


class TestFetchStatus:
  def test_success_clears_last_error(self):
    fid = repo.add_feed(FeedRow(name="F", url="https://f.example.com/")).id
    repo.update_feed_fetch_status(fid, success=False, error="boom")
    repo.update_feed_fetch_status(fid, success=True)
    feed = repo.get_feed_by_id(fid)
    assert feed is not None
    assert feed.last_error is None
    assert feed.last_fetched_at is not None

  def test_failure_sets_error_without_touching_failure_counter(self):
    # Manual single-feed fetch must not pollute the adaptive-schedule signal.
    # consecutive_failures is owned by record_feed_attempt (scheduled path).
    fid = repo.add_feed(FeedRow(name="F", url="https://f.example.com/")).id
    repo.update_feed_fetch_status(fid, success=False, error="boom")
    repo.update_feed_fetch_status(fid, success=False, error="boom again")
    feed = repo.get_feed_by_id(fid)
    assert feed is not None
    assert feed.consecutive_failures == 0
    assert feed.last_error == "boom again"


class TestFilterCrud:
  def test_add_and_list(self):
    repo.add_filter("spam")
    repo.add_filter(r"/\bad\b/", target="body")
    rows = repo.list_filters()
    assert [(r.pattern, r.target) for r in rows] == [("spam", "title"), (r"/\bad\b/", "body")]

  def test_delete(self):
    added = repo.add_filter("spam")
    repo.delete_filter(added.id)
    assert repo.list_filters() == []


class TestDeleteAllData:
  def test_clears_feeds_articles_folders(self):
    folder = repo.create_folder("X")
    fid = repo.add_feed(FeedRow(name="F", url="https://f.example.com/", folder_id=folder.id)).id
    with db.connection() as conn:
      conn.execute(
        "INSERT INTO articles (url, feed_id, title, source) VALUES ('u1', %s, 'T', 'F')",
        (fid,),
      )
    repo.delete_all_data()
    assert repo.list_feeds() == []
    assert repo.list_folders() == []
    assert repo.list_articles() == []
