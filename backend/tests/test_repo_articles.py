"""Integration tests for article upsert, read-state, and refresh-job repo functions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import db
import repo
from models import Article


def _make_feed(*, name: str = "F", translate: bool = False, summarize: bool = True) -> int:
  with db.connection() as conn:
    row = conn.execute(
      """INSERT INTO feeds (name, url, translate, summarize, enabled)
         VALUES (%s, %s, %s, %s, TRUE) RETURNING id""",
      (name, f"https://{name}.example.com/rss", translate, summarize),
    ).fetchone()
    assert row is not None
    return int(row["id"])


def _art(
  *,
  url: str,
  source: str = "F",
  title: str = "T",
  title_translated: str = "",
  summary: str = "",
  content_html: str = "",
  content_translated: str = "",
  content_snippet: str = "body",
  published: datetime | None = None,
) -> Article:
  return Article(
    title=title,
    url=url,
    source=source,
    published=published or datetime.now(tz=timezone.utc),
    content_snippet=content_snippet,
    title_translated=title_translated,
    summary=summary,
    content_html=content_html,
    content_translated=content_translated,
  )


pytestmark = pytest.mark.usefixtures("clean_db")


class TestUpsertArticles:
  def test_inserts_new_articles_and_reports_count(self):
    fid = _make_feed(name="F")
    n = repo.upsert_articles([_art(url="u1"), _art(url="u2")], {"F": fid})
    assert n == 2
    assert len(repo.list_articles()) == 2

  def test_empty_list_returns_zero(self):
    assert repo.upsert_articles([]) == 0

  def test_duplicate_url_does_not_count_as_new(self):
    fid = _make_feed(name="F")
    repo.upsert_articles([_art(url="u1", content_snippet="first")], {"F": fid})
    n = repo.upsert_articles([_art(url="u1", content_snippet="second")], {"F": fid})
    assert n == 0

  def test_update_fills_missing_fields_without_overwriting(self):
    # The upsert uses COALESCE(EXCLUDED, existing): a NULL-ish incoming value
    # must NOT wipe an existing translated/summary field.
    fid = _make_feed(name="F")
    repo.upsert_articles(
      [_art(url="u1", title_translated="訳1", summary="要約1", content_snippet="a")],
      {"F": fid},
    )
    # Second pass: empty summary/translation must be preserved; content_html gets filled.
    repo.upsert_articles(
      [_art(url="u1", content_html="<p>hi</p>", content_snippet="")],
      {"F": fid},
    )
    rows = repo.list_articles()
    assert len(rows) == 1
    a = rows[0]
    assert a.title_translated == "訳1"
    assert a.summary == "要約1"
    assert a.content_html == "<p>hi</p>"

  def test_feed_id_mapping_links_article_to_feed(self):
    fid = _make_feed(name="F")
    repo.upsert_articles([_art(url="u1", source="F")], {"F": fid})
    rows = repo.list_articles(feed_id=fid)
    assert [r.url for r in rows] == ["u1"]

  def test_unknown_source_has_null_feed_id(self):
    repo.upsert_articles([_art(url="u1", source="unknown")], {})
    rows = repo.list_articles()
    assert rows[0].feed_id is None


class TestReadState:
  def test_mark_read_sets_read_at(self):
    fid = _make_feed()
    repo.upsert_articles([_art(url="u1"), _art(url="u2")], {"F": fid})
    repo.mark_read(["u1"])
    rows = {r.url: r for r in repo.list_articles()}
    assert rows["u1"].read_at is not None
    assert rows["u2"].read_at is None

  def test_mark_unread_clears_read_at(self):
    fid = _make_feed()
    repo.upsert_articles([_art(url="u1")], {"F": fid})
    repo.mark_read(["u1"])
    repo.mark_unread(["u1"])
    assert repo.list_articles()[0].read_at is None

  def test_mark_all_read_scoped_to_feed(self):
    f1 = _make_feed(name="A")
    f2 = _make_feed(name="B")
    repo.upsert_articles([_art(url="a1", source="A")], {"A": f1})
    repo.upsert_articles([_art(url="b1", source="B")], {"B": f2})
    n = repo.mark_all_read(feed_id=f1)
    assert n == 1
    by_url = {r.url: r for r in repo.list_articles()}
    assert by_url["a1"].read_at is not None
    assert by_url["b1"].read_at is None

  def test_mark_all_read_global(self):
    fid = _make_feed()
    repo.upsert_articles([_art(url="u1"), _art(url="u2")], {"F": fid})
    assert repo.mark_all_read() == 2
    assert all(r.read_at is not None for r in repo.list_articles())

  def test_list_articles_unread_filter(self):
    fid = _make_feed()
    repo.upsert_articles([_art(url="u1"), _art(url="u2")], {"F": fid})
    repo.mark_read(["u1"])
    unread = repo.list_articles(unread=True)
    assert [r.url for r in unread] == ["u2"]


class TestMarkReadEdgeCases:
  def test_empty_urls_is_noop(self):
    # Guard against accidental "UPDATE ... WHERE url IN ()" style bugs.
    repo.mark_read([])
    repo.mark_unread([])


class TestPruneArticles:
  def test_deletes_only_old_articles(self):
    fid = _make_feed()
    repo.upsert_articles([_art(url="fresh")], {"F": fid})
    # Backdate one directly.
    with db.connection() as conn:
      conn.execute(
        """INSERT INTO articles (url, feed_id, title, source, fetched_at)
           VALUES (%s, %s, %s, %s, %s)""",
        ("old", fid, "T", "F", datetime.now(tz=timezone.utc) - timedelta(days=60)),
      )
    n = repo.prune_articles(max_age_days=30)
    assert n == 1
    assert {r.url for r in repo.list_articles()} == {"fresh"}

  def test_zero_disables_pruning(self):
    fid = _make_feed()
    with db.connection() as conn:
      conn.execute(
        """INSERT INTO articles (url, feed_id, title, source, fetched_at)
           VALUES (%s, %s, %s, %s, %s)""",
        ("ancient", fid, "T", "F", datetime.now(tz=timezone.utc) - timedelta(days=3650)),
      )
    assert repo.prune_articles(max_age_days=0) == 0
    assert {r.url for r in repo.list_articles()} == {"ancient"}


class TestRefreshJobs:
  def test_get_latest_is_none_when_empty(self):
    assert repo.get_latest_refresh_job() is None

  def test_create_then_finish_roundtrip(self):
    job_id = repo.create_refresh_job("web")
    assert job_id > 0
    latest = repo.get_latest_refresh_job()
    assert latest is not None
    assert latest.status == "running"
    assert latest.finished_at is None

    repo.finish_refresh_job(job_id, "success", new_count=3, error=None)
    latest = repo.get_latest_refresh_job()
    assert latest is not None
    assert latest.status == "success"
    assert latest.new_count == 3
    assert latest.finished_at is not None

  def test_get_latest_returns_highest_id(self):
    repo.create_refresh_job("web")
    second = repo.create_refresh_job("cron")
    latest = repo.get_latest_refresh_job()
    assert latest is not None
    assert latest.id == second
    assert latest.source == "cron"
