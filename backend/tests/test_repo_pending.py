"""Integration tests for repo.list_pending_summaries.

Regression guard: the SQL predicate MUST mirror fetcher._needs_llm.
A prior bug let already-processed short articles bounce back into the queue,
starving truly-pending items.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import db
import repo


def _make_feed(*, translate: bool, summarize: bool, name: str = "F") -> int:
  with db.connection() as conn:
    row = conn.execute(
      """INSERT INTO feeds (name, url, translate, summarize, enabled)
         VALUES (%s, %s, %s, %s, TRUE) RETURNING id""",
      (name, f"https://{name}.example.com/rss", translate, summarize),
    ).fetchone()
    assert row is not None
    return int(row["id"])


def _make_article(
  *,
  feed_id: int,
  url: str,
  title: str = "T",
  title_translated: str | None = None,
  content_snippet: str | None = None,
  summary: str | None = None,
  content_translated: str | None = None,
  published: datetime | None = None,
) -> None:
  with db.connection() as conn:
    conn.execute(
      """INSERT INTO articles
           (url, feed_id, title, title_translated, source, published,
            content_snippet, summary, content_translated)
         VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
      (
        url, feed_id, title, title_translated, "test",
        published or datetime.now(tz=timezone.utc),
        content_snippet, summary, content_translated,
      ),
    )


def _urls(rows: list[object]) -> set[str]:
  return {getattr(r, "url") for r in rows}


pytestmark = pytest.mark.usefixtures("clean_db")


class TestSummarizeDisabled:
  def test_summarize_false_feed_never_pending(self):
    fid = _make_feed(translate=True, summarize=False)
    _make_article(feed_id=fid, url="u1", content_snippet="x" * 500)
    assert repo.list_pending_summaries() == []


class TestTranslatingFeed:
  def test_missing_title_translation_is_pending(self):
    fid = _make_feed(translate=True, summarize=True)
    _make_article(feed_id=fid, url="u1", content_snippet="short")
    assert _urls(repo.list_pending_summaries()) == {"u1"}

  def test_title_translated_but_no_summary_or_content_is_pending(self):
    fid = _make_feed(translate=True, summarize=True)
    _make_article(
      feed_id=fid, url="u1",
      title_translated="訳", content_snippet="any",
    )
    assert _urls(repo.list_pending_summaries()) == {"u1"}

  def test_title_translated_and_content_translated_not_pending(self):
    # Even without summary, having content_translated is enough for the "short path".
    fid = _make_feed(translate=True, summarize=True)
    _make_article(
      feed_id=fid, url="u1",
      title_translated="訳", content_translated="翻訳済み本文",
    )
    assert repo.list_pending_summaries() == []

  def test_title_translated_and_summary_not_pending(self):
    fid = _make_feed(translate=True, summarize=True)
    _make_article(
      feed_id=fid, url="u1",
      title_translated="訳", summary="要約",
    )
    assert repo.list_pending_summaries() == []


class TestNonTranslatingFeed:
  def test_short_article_without_summary_not_pending(self):
    # Length < 300 → skipped by _needs_llm; pending query must agree.
    fid = _make_feed(translate=False, summarize=True)
    _make_article(feed_id=fid, url="u1", content_snippet="短い")
    assert repo.list_pending_summaries() == []

  def test_long_article_without_summary_is_pending(self):
    fid = _make_feed(translate=False, summarize=True)
    _make_article(feed_id=fid, url="u1", content_snippet="x" * 400)
    assert _urls(repo.list_pending_summaries()) == {"u1"}

  def test_long_article_with_summary_not_pending(self):
    fid = _make_feed(translate=False, summarize=True)
    _make_article(feed_id=fid, url="u1", content_snippet="x" * 400, summary="done")
    assert repo.list_pending_summaries() == []

  def test_null_content_snippet_not_pending(self):
    fid = _make_feed(translate=False, summarize=True)
    _make_article(feed_id=fid, url="u1", content_snippet=None)
    assert repo.list_pending_summaries() == []


class TestOrderingAndLimit:
  def test_orders_by_published_ascending(self):
    fid = _make_feed(translate=True, summarize=True)
    now = datetime.now(tz=timezone.utc)
    _make_article(feed_id=fid, url="new", published=now)
    _make_article(feed_id=fid, url="old", published=now - timedelta(days=2))
    _make_article(feed_id=fid, url="mid", published=now - timedelta(days=1))
    rows = repo.list_pending_summaries(limit=10)
    assert [r.url for r in rows] == ["old", "mid", "new"]

  def test_respects_limit(self):
    fid = _make_feed(translate=True, summarize=True)
    for i in range(5):
      _make_article(feed_id=fid, url=f"u{i}")
    assert len(repo.list_pending_summaries(limit=2)) == 2
