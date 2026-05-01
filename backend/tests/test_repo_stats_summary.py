"""Integration tests for get_feed_stats and update_article_summary."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import db
import repo
from schemas import FeedRow


pytestmark = pytest.mark.usefixtures("clean_db")


def _feed(name: str = "F") -> int:
  return repo.add_feed(FeedRow(name=name, url=f"https://{name}.example.com/")).id


def _insert_article(
  feed_id: int | None,
  *,
  url: str,
  published: datetime | None = None,
  read: bool = False,
) -> None:
  with db.connection() as conn:
    conn.execute(
      """INSERT INTO articles (url, feed_id, title, source, published, read_at)
         VALUES (%s, %s, 'T', 'F', %s, %s)""",
      (url, feed_id, published, datetime.now(tz=timezone.utc) if read else None),
    )


class TestFeedStats:
  def test_empty_returns_empty_map(self):
    _feed()  # Feed with no articles
    assert repo.get_feed_stats() == {}

  def test_counts_articles_and_unread(self):
    fid = _feed()
    _insert_article(fid, url="u1", read=False)
    _insert_article(fid, url="u2", read=True)
    _insert_article(fid, url="u3", read=False)
    stats = repo.get_feed_stats()
    assert fid in stats
    s = stats[fid]
    assert s.article_count == 3
    assert s.unread_count == 2

  def test_latest_and_oldest_published(self):
    fid = _feed()
    now = datetime.now(tz=timezone.utc)
    _insert_article(fid, url="old", published=now - timedelta(days=5))
    _insert_article(fid, url="new", published=now - timedelta(hours=1))
    _insert_article(fid, url="mid", published=now - timedelta(days=2))
    s = repo.get_feed_stats()[fid]
    assert s.latest_published is not None
    assert s.oldest_published is not None
    assert s.latest_published > s.oldest_published
    # Within a second tolerance for TZ/clock drift.
    assert abs((s.latest_published - (now - timedelta(hours=1))).total_seconds()) < 2
    assert abs((s.oldest_published - (now - timedelta(days=5))).total_seconds()) < 2

  def test_feeds_without_articles_omitted(self):
    a = _feed("A")
    b = _feed("B")
    _insert_article(a, url="u1")
    stats = repo.get_feed_stats()
    assert a in stats
    assert b not in stats

  def test_articles_with_null_feed_id_excluded(self):
    # Orphan articles (feed_id IS NULL) must not appear in the stats map.
    fid = _feed()
    _insert_article(fid, url="u1")
    _insert_article(None, url="orphan")
    stats = repo.get_feed_stats()
    assert list(stats.keys()) == [fid]
    assert stats[fid].article_count == 1

  def test_filters_excluded_from_counts(self):
    # Counts must agree with what list_articles surfaces. If every unread
    # article matches an NG-word filter, the badge should read 0 unread —
    # otherwise the user sees "39 unread" beside an "All caught up" list.
    fid = _feed()
    with db.connection() as conn:
      conn.execute(
        """INSERT INTO articles (url, feed_id, title, source) VALUES
           (%s, %s, %s, 'F'), (%s, %s, %s, 'F')""",
        ("ng-1", fid, "ネタバレ注意 something", "ng-2", fid, "another ネタバレ注意"),
      )
      conn.execute(
        "INSERT INTO articles (url, feed_id, title, source) VALUES (%s, %s, 'clean', 'F')",
        ("clean", fid),
      )
      conn.execute(
        "INSERT INTO filters (pattern, target) VALUES ('ネタバレ注意', 'title')",
      )
    stats = repo.get_feed_stats()
    assert stats[fid].article_count == 1
    assert stats[fid].unread_count == 1

  def test_feed_drops_out_when_all_articles_filtered(self):
    fid = _feed()
    with db.connection() as conn:
      conn.execute(
        "INSERT INTO articles (url, feed_id, title, source) VALUES (%s, %s, 'NG word', 'F')",
        ("only", fid),
      )
      conn.execute(
        "INSERT INTO filters (pattern, target) VALUES ('NG word', 'title')",
      )
    # Feed has only filter-matched articles → no entry in stats (parallels
    # `test_feeds_without_articles_omitted` semantics).
    assert repo.get_feed_stats() == {}


class TestUpdateArticleSummary:
  def _base(self) -> str:
    fid = _feed()
    with db.connection() as conn:
      conn.execute(
        "INSERT INTO articles (url, feed_id, title, source) VALUES ('u1', %s, 'T', 'F')",
        (fid,),
      )
    return "u1"

  def test_updates_all_fields(self):
    url = self._base()
    repo.update_article_summary(url, "訳", "要約", "翻訳本文")
    rows = repo.list_articles()
    assert rows[0].title_translated == "訳"
    assert rows[0].summary == "要約"
    assert rows[0].content_translated == "翻訳本文"

  def test_none_preserves_existing(self):
    # COALESCE(%s, existing): passing None must not overwrite existing values.
    url = self._base()
    repo.update_article_summary(url, "訳1", "要約1", "翻訳1")
    repo.update_article_summary(url, None, None, None)
    rows = repo.list_articles()
    assert rows[0].title_translated == "訳1"
    assert rows[0].summary == "要約1"
    assert rows[0].content_translated == "翻訳1"

  def test_partial_update(self):
    url = self._base()
    repo.update_article_summary(url, "訳", None, None)
    rows = repo.list_articles()
    assert rows[0].title_translated == "訳"
    assert rows[0].summary is None
    assert rows[0].content_translated is None

  def test_unknown_url_silently_updates_nothing(self):
    # UPDATE with no matching WHERE is not an error in SQL; document the behavior.
    repo.update_article_summary("does-not-exist", "訳", "要約", None)
    assert repo.list_articles() == []
