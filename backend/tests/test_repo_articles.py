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

  def test_mixed_batch_counts_only_new(self):
    # Batched multi-row INSERT must distinguish inserts from updates within
    # the same statement (xmax = 0 per row) and return the new-row count.
    fid = _make_feed(name="F")
    repo.upsert_articles([_art(url="u1"), _art(url="u2")], {"F": fid})
    batch = [_art(url=u) for u in ("u1", "u2", "u3", "u4", "u5")]
    n = repo.upsert_articles(batch, {"F": fid})
    assert n == 3
    assert len(repo.list_articles()) == 5

  def test_duplicate_urls_in_same_batch_are_collapsed(self):
    # PostgreSQL's `ON CONFLICT DO UPDATE` rejects two rows in one statement
    # that target the same conflict key, so the implementation must collapse
    # duplicates before the multi-row INSERT.
    fid = _make_feed(name="F")
    n = repo.upsert_articles(
      [_art(url="u1", summary="first"), _art(url="u1", summary="second")],
      {"F": fid},
    )
    assert n == 1
    rows = repo.list_articles()
    assert len(rows) == 1
    # `summary` is in the COALESCE-managed set, so later non-empty wins.
    assert rows[0].summary == "second"

  def test_duplicate_urls_keep_first_rows_insert_only_columns(self):
    # ON CONFLICT DO UPDATE only rewrites content_*/title_translated/summary
    # /content_translated. Under the row-by-row loop, the first row's title /
    # source / published (and the derived feed_id) would stick across a
    # same-batch dup. The merged-batch path must preserve that.
    f1 = _make_feed(name="A")
    _make_feed(name="B")
    early = datetime(2026, 1, 1, tzinfo=timezone.utc)
    late = datetime(2026, 6, 1, tzinfo=timezone.utc)
    n = repo.upsert_articles(
      [
        _art(url="u1", title="first-title", source="A", summary="", published=early),
        _art(url="u1", title="second-title", source="B", summary="late", published=late),
      ],
      {"A": f1},
    )
    assert n == 1
    rows = repo.list_articles()
    assert len(rows) == 1
    assert rows[0].title == "first-title"
    assert rows[0].source == "A"
    assert rows[0].feed_id == f1
    assert rows[0].published == early
    # COALESCE-managed column still picks up the later non-empty value.
    assert rows[0].summary == "late"

  def test_duplicate_urls_merge_preserves_earlier_non_empty_field(self):
    # A later empty value MUST NOT clobber an earlier non-empty COALESCE
    # column — the row-by-row loop's COALESCE would have preserved it.
    fid = _make_feed(name="F")
    n = repo.upsert_articles(
      [
        _art(url="u1", summary="rich", content_html="<p>full</p>"),
        _art(url="u1", summary="", content_html=""),
      ],
      {"F": fid},
    )
    assert n == 1
    rows = repo.list_articles()
    assert len(rows) == 1
    assert rows[0].summary == "rich"
    assert rows[0].content_html == "<p>full</p>"

  def test_duplicate_urls_merge_later_value_wins_when_both_present(self):
    fid = _make_feed(name="F")
    repo.upsert_articles(
      [
        _art(url="u1", summary="early"),
        _art(url="u1", summary="late"),
      ],
      {"F": fid},
    )
    rows = repo.list_articles()
    assert rows[0].summary == "late"

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


class TestSinceDaysFilter:
  def test_none_returns_all(self):
    fid = _make_feed()
    repo.upsert_articles([_art(url="u1"), _art(url="u2")], {"F": fid})
    assert len(repo.list_articles(since_days=None)) == 2

  def test_filters_by_published(self):
    fid = _make_feed()
    now = datetime.now(tz=timezone.utc)
    repo.upsert_articles(
      [
        _art(url="recent", published=now - timedelta(days=2)),
        _art(url="old", published=now - timedelta(days=30)),
      ],
      {"F": fid},
    )
    urls = {r.url for r in repo.list_articles(since_days=7)}
    assert urls == {"recent"}

  def test_published_null_falls_back_to_fetched_at(self):
    # COALESCE(published, fetched_at) — a row with no published date is judged
    # by ingest time. Recent fetch survives a 7-day window even without
    # published; an old fetch is excluded.
    fid = _make_feed()
    now = datetime.now(tz=timezone.utc)
    with db.connection() as conn:
      conn.execute(
        """INSERT INTO articles (url, feed_id, title, source, published, fetched_at)
           VALUES (%s, %s, %s, %s, NULL, %s)""",
        ("fresh-no-pub", fid, "T", "F", now - timedelta(days=1)),
      )
      conn.execute(
        """INSERT INTO articles (url, feed_id, title, source, published, fetched_at)
           VALUES (%s, %s, %s, %s, NULL, %s)""",
        ("stale-no-pub", fid, "T", "F", now - timedelta(days=30)),
      )
    urls = {r.url for r in repo.list_articles(since_days=7)}
    assert urls == {"fresh-no-pub"}

  def test_combined_with_unread_and_feed_id(self):
    f1 = _make_feed(name="A")
    f2 = _make_feed(name="B")
    now = datetime.now(tz=timezone.utc)
    repo.upsert_articles(
      [
        _art(url="a-recent", source="A", published=now - timedelta(days=1)),
        _art(url="a-old", source="A", published=now - timedelta(days=30)),
        _art(url="b-recent", source="B", published=now - timedelta(days=1)),
      ],
      {"A": f1, "B": f2},
    )
    repo.mark_read(["a-recent"])
    rows = repo.list_articles(feed_id=f1, unread=True, since_days=7)
    # f1 + unread + within 7 days → only "a-recent" is in window but it's read,
    # "a-old" is unread but outside the window. So nothing matches.
    assert rows == []


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


class TestListArticlesOrderStability:
  """Tie-breakers in the ORDER BY (`fetched_at DESC, url DESC`) keep the
  result deterministic when rows share `COALESCE(published, fetched_at)`.
  The repo `reversed()`s the SQL slice before returning, so within ties the
  API-visible direction is `fetched_at ASC, url ASC`.
  """

  def _force_timestamps(
    self,
    url: str,
    *,
    published: datetime | None,
    fetched_at: datetime,
  ) -> None:
    # `fetched_at` defaults to NOW() on insert, so the test pins it via
    # UPDATE to construct exact tie configurations without relying on
    # wall-clock spacing.
    with db.connection() as conn:
      conn.execute(
        "UPDATE articles SET published = %s, fetched_at = %s WHERE url = %s",
        (published, fetched_at, url),
      )

  def test_full_tie_order_is_stable_and_url_asc(self):
    fid = _make_feed()
    repo.upsert_articles(
      [_art(url="u-c"), _art(url="u-a"), _art(url="u-b")],
      {"F": fid},
    )
    pub = datetime(2026, 5, 20, 8, 0, 0, tzinfo=timezone.utc)
    fetched = datetime(2026, 5, 20, 14, 30, 0, tzinfo=timezone.utc)
    for u in ("u-a", "u-b", "u-c"):
      self._force_timestamps(u, published=pub, fetched_at=fetched)

    first = [r.url for r in repo.list_articles()]
    second = [r.url for r in repo.list_articles()]
    assert first == second
    # API returns oldest-first (`reversed()` after SQL `... url DESC`), so
    # within a full tie the visible order is `url ASC`.
    assert first == ["u-a", "u-b", "u-c"]

  def test_limit_boundary_set_is_stable(self):
    fid = _make_feed()
    repo.upsert_articles(
      [_art(url="u-c"), _art(url="u-a"), _art(url="u-b")],
      {"F": fid},
    )
    pub = datetime(2026, 5, 20, 8, 0, 0, tzinfo=timezone.utc)
    fetched = datetime(2026, 5, 20, 14, 30, 0, tzinfo=timezone.utc)
    for u in ("u-a", "u-b", "u-c"):
      self._force_timestamps(u, published=pub, fetched_at=fetched)

    # SQL keeps the top `limit` after `... url DESC`, so {u-c, u-b} survive
    # the cut. Compare as lists so the order within the surviving slice is
    # locked too — a future change can't silently flip the visible order.
    first = [r.url for r in repo.list_articles(limit=2)]
    second = [r.url for r in repo.list_articles(limit=2)]
    assert first == second
    # `reversed()` after SQL `... url DESC` → API yields `url ASC` inside
    # the kept tie group.
    assert first == ["u-b", "u-c"]

  def test_fetched_at_breaks_published_tie(self):
    fid = _make_feed()
    repo.upsert_articles(
      [_art(url="u-old-fetch"), _art(url="u-new-fetch")],
      {"F": fid},
    )
    pub = datetime(2026, 5, 20, 8, 0, 0, tzinfo=timezone.utc)
    self._force_timestamps(
      "u-old-fetch",
      published=pub,
      fetched_at=datetime(2026, 5, 20, 10, 0, 0, tzinfo=timezone.utc),
    )
    self._force_timestamps(
      "u-new-fetch",
      published=pub,
      fetched_at=datetime(2026, 5, 20, 14, 0, 0, tzinfo=timezone.utc),
    )

    # SQL `fetched_at DESC` → newer fetch first; `reversed()` → API yields
    # older fetch first.
    urls = [r.url for r in repo.list_articles()]
    assert urls == ["u-old-fetch", "u-new-fetch"]

  def test_published_null_group_orders_by_fetched_at(self):
    fid = _make_feed()
    repo.upsert_articles(
      [_art(url="u-early"), _art(url="u-late")],
      {"F": fid},
    )
    self._force_timestamps(
      "u-early",
      published=None,
      fetched_at=datetime(2026, 5, 20, 9, 0, 0, tzinfo=timezone.utc),
    )
    self._force_timestamps(
      "u-late",
      published=None,
      fetched_at=datetime(2026, 5, 20, 15, 0, 0, tzinfo=timezone.utc),
    )

    first = [r.url for r in repo.list_articles()]
    second = [r.url for r in repo.list_articles()]
    assert first == second
    # COALESCE falls back to `fetched_at`; API returns oldest-first.
    assert first == ["u-early", "u-late"]
