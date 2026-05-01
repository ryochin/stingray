"""Repository functions for feeds, articles, and refresh jobs."""

from __future__ import annotations

import math
import random
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Literal

from psycopg import sql

from models import Article
from schemas import ArticleRow, FeedRow, FeedStats, FilterRow, FolderRow, RefreshJob

import db


# -- Adaptive fetch scheduling --

MIN_INTERVAL_MIN = 10
MAX_INTERVAL_MIN = 360  # 6 hours
BUCKETS = (10, 30, 60, 120, 240, 360)
JITTER_RATIO = 0.10
CRON_TICK_MIN = 10
REFRESH_LOCK_KEY = 0xFEEDCAFE  # bigint key for pg_try_advisory_lock

Outcome = Literal["fresh", "miss", "degraded", "failure"]


def step_bucket(cur_min: int, direction: int) -> int:
  """Move one step along BUCKETS; clamp at the endpoints.

  direction > 0 enlarges the interval (miss), direction < 0 shrinks (fresh).
  """
  try:
    idx = BUCKETS.index(cur_min)
  except ValueError:
    # If cur_min isn't a bucket (shouldn't happen due to CHECK), pick the
    # nearest bucket without underflowing.
    idx = min(range(len(BUCKETS)), key=lambda i: abs(BUCKETS[i] - cur_min))
  new_idx = max(0, min(len(BUCKETS) - 1, idx + (1 if direction > 0 else -1)))
  return BUCKETS[new_idx]


def schedule_next_at(now: datetime, interval_min: int) -> datetime:
  """Apply ±JITTER_RATIO jitter, then ceil to the next CRON_TICK_MIN boundary.

  Tick alignment ensures that schedules land on the cron cadence rather than
  between ticks (where they would effectively wait until the next tick).
  """
  factor = 1 + random.uniform(-JITTER_RATIO, JITTER_RATIO)
  raw = now + timedelta(minutes=interval_min * factor)
  tick_s = CRON_TICK_MIN * 60
  snapped = math.ceil(raw.timestamp() / tick_s) * tick_s
  return datetime.fromtimestamp(snapped, tz=timezone.utc)


def record_feed_attempt(
  feed_id: int,
  outcome: Outcome,
  *,
  error: str | None = None,
) -> None:
  """Update a feed's schedule and health columns based on a fetch outcome.

  Centralizes every write to fetch_interval_min / next_fetch_at /
  consecutive_failures / last_error / last_fetched_at so concurrent paths
  cannot step on each other. Selects FOR UPDATE to serialize with toggle
  and other feed-row writes.
  """
  with db.connection() as conn, conn.transaction():
    row = conn.execute(
      "SELECT fetch_interval_min, consecutive_failures, next_fetch_at "
      "FROM feeds WHERE id = %s FOR UPDATE",
      (feed_id,),
    ).fetchone()
    if row is None:
      return
    cur_min = row["fetch_interval_min"]
    fails = row["consecutive_failures"]
    cur_next = row["next_fetch_at"]
    now = datetime.now(tz=timezone.utc)

    if outcome == "failure":
      n = fails + 1
      # Exponential backoff capped at MAX_INTERVAL_MIN: 15, 30, 60, 120, 240, 360.
      backoff = min(MAX_INTERVAL_MIN, 15 * 2 ** max(n - 1, 0))
      # Never pull the next attempt earlier than the current cadence or a
      # previously-scheduled future time.
      raw_next = schedule_next_at(now, max(backoff, cur_min))
      if cur_next is not None and cur_next > raw_next:
        raw_next = cur_next
      conn.execute(
        "UPDATE feeds SET next_fetch_at = %s, consecutive_failures = %s, "
        "last_error = %s WHERE id = %s",
        (raw_next, n, error, feed_id),
      )
      return

    if outcome == "degraded":
      # Neutral: keep bucket and fail counter, advance schedule at current
      # cadence. last_error only written when the caller supplies one (e.g.
      # web-norules) so transient cache fallbacks don't wipe diagnostic info.
      next_at = schedule_next_at(now, cur_min)
      if error is not None:
        conn.execute(
          "UPDATE feeds SET next_fetch_at = %s, last_fetched_at = %s, "
          "last_error = %s WHERE id = %s",
          (next_at, now, error, feed_id),
        )
      else:
        conn.execute(
          "UPDATE feeds SET next_fetch_at = %s, last_fetched_at = %s "
          "WHERE id = %s",
          (next_at, now, feed_id),
        )
      return

    # fresh / miss: move the bucket and clear failure state.
    next_min = step_bucket(cur_min, direction=+1 if outcome == "miss" else -1)
    next_at = schedule_next_at(now, next_min)
    conn.execute(
      "UPDATE feeds SET fetch_interval_min = %s, next_fetch_at = %s, "
      "consecutive_failures = 0, last_fetched_at = %s, last_error = NULL "
      "WHERE id = %s",
      (next_min, next_at, now, feed_id),
    )


def list_due_feeds(force: bool = False) -> list[FeedRow]:
  """Return enabled feeds that are currently due for fetch.

  force=True returns every enabled feed (used by manual Refresh button).
  force=False returns only feeds whose next_fetch_at is NULL or past.
  """
  with db.connection() as conn:
    if force:
      rows = conn.execute(
        f"SELECT * FROM feeds WHERE enabled {_FEED_ORDER}"
      ).fetchall()
    else:
      rows = conn.execute(
        f"SELECT * FROM feeds "
        f"WHERE enabled AND (next_fetch_at IS NULL OR next_fetch_at <= NOW()) "
        f"{_FEED_ORDER}"
      ).fetchall()
    return [_row_to_feed(r) for r in rows]


@contextmanager
def advisory_lock(key: int = REFRESH_LOCK_KEY) -> Iterator[bool]:
  """Context manager that acquires a session-scoped Postgres advisory lock
  and releases it on exit. Yields True if the lock was acquired, False if
  already held by another session.

  The same pooled connection is held for the duration of the `with` block so
  both acquire and release happen on the same session — session-scoped
  advisory locks would otherwise leak across pool connection reuse.
  """
  with db.connection() as conn:
    row = conn.execute("SELECT pg_try_advisory_lock(%s) AS got", (key,)).fetchone()
    got = bool(row and row["got"])
    try:
      yield got
    finally:
      if got:
        conn.execute("SELECT pg_advisory_unlock(%s)", (key,))


# -- Filters --


def list_filters() -> list[FilterRow]:
  with db.connection() as conn:
    rows = conn.execute("SELECT * FROM filters ORDER BY id").fetchall()
    return [FilterRow(**r) for r in rows]


def add_filter(pattern: str, target: str = "title") -> FilterRow:
  with db.connection() as conn:
    row = conn.execute(
      "INSERT INTO filters (pattern, target) VALUES (%s, %s) RETURNING *",
      (pattern, target),
    ).fetchone()
    if row is None:
      raise RuntimeError("add_filter: INSERT returned no row")
    return FilterRow.model_validate(row)


def delete_filter(filter_id: int) -> None:
  with db.connection() as conn:
    conn.execute("DELETE FROM filters WHERE id = %s", (filter_id,))


def _parse_filter_pattern(pattern: str) -> tuple[bool, str]:
  if len(pattern) > 2 and pattern.startswith("/") and pattern.endswith("/"):
    return True, pattern[1:-1]
  return False, pattern


def _article_matches_filter(article: ArticleRow, filters: list[FilterRow]) -> bool:
  for f in filters:
    is_regex, pat = _parse_filter_pattern(f.pattern)
    if f.target == "title":
      texts = [article.title, article.title_translated or ""]
    else:
      texts = [article.title, article.title_translated or "",
               article.content_snippet or "", article.summary or ""]
    for text in texts:
      if not text:
        continue
      if is_regex:
        try:
          if len(pat) > 200:
            continue
          if re.search(pat, text[:10_000], re.IGNORECASE):
            return True
        except re.error:
          continue
      else:
        if pat.lower() in text.lower():
          return True
  return False


# -- Folders --


def list_folders() -> list[FolderRow]:
  with db.connection() as conn:
    rows = conn.execute("SELECT * FROM folders ORDER BY position, id").fetchall()
    return [FolderRow(**r) for r in rows]


def create_folder(name: str) -> FolderRow:
  with db.connection() as conn:
    row = conn.execute(
      """INSERT INTO folders (name, position)
         VALUES (%s, COALESCE((SELECT MAX(position) + 1 FROM folders), 0))
         RETURNING *""",
      (name,),
    ).fetchone()
    if row is None:
      raise RuntimeError("create_folder: INSERT returned no row")
    return FolderRow.model_validate(row)


def rename_folder(folder_id: int, name: str) -> None:
  with db.connection() as conn:
    conn.execute("UPDATE folders SET name = %s WHERE id = %s", (name, folder_id))


def delete_folder(folder_id: int) -> None:
  with db.connection() as conn:
    conn.execute("DELETE FROM folders WHERE id = %s", (folder_id,))


def reorder_folders(folder_ids: list[int]) -> None:
  if not folder_ids:
    return
  with db.connection() as conn, conn.cursor() as cur:
    cur.executemany(
      "UPDATE folders SET position = %s WHERE id = %s",
      [(i, fid) for i, fid in enumerate(folder_ids)],
    )


# -- Feeds --


def move_feed_to_folder(feed_id: int, folder_id: int | None) -> None:
  with db.connection() as conn:
    conn.execute("UPDATE feeds SET folder_id = %s WHERE id = %s", (folder_id, feed_id))


def _row_to_feed(r: dict[str, object]) -> FeedRow:
  return FeedRow.model_validate(r)


_FEED_ORDER = "ORDER BY position, id"


def list_feeds(*, enabled: bool | None = None) -> list[FeedRow]:
  with db.connection() as conn:
    if enabled is None:
      rows = conn.execute(f"SELECT * FROM feeds {_FEED_ORDER}").fetchall()
    else:
      rows = conn.execute(
        f"SELECT * FROM feeds WHERE enabled = %s {_FEED_ORDER}",
        (enabled,),
      ).fetchall()
    return [_row_to_feed(r) for r in rows]


def get_feed_by_id(feed_id: int) -> FeedRow | None:
  with db.connection() as conn:
    row = conn.execute("SELECT * FROM feeds WHERE id = %s", (feed_id,)).fetchone()
    return _row_to_feed(row) if row else None


def add_feed(feed: FeedRow) -> FeedRow:
  """Insert a new feed at the end of the list. Returns the persisted row."""
  with db.connection() as conn:
    row = conn.execute(
      """INSERT INTO feeds
           (name, url, site_url, translate, summarize, enabled, folder_id, position, extraction_rules)
         VALUES
           (%s, %s, %s, %s, %s, %s, %s,
            COALESCE((SELECT MAX(position) + 1 FROM feeds), 0),
            %s)
         RETURNING *""",
      (
        feed.name,
        feed.url,
        feed.site_url,
        feed.translate,
        feed.summarize,
        feed.enabled,
        feed.folder_id,
        feed.extraction_rules,
      ),
    ).fetchone()
    if row is None:
      raise RuntimeError("add_feed: INSERT returned no row")
    return _row_to_feed(row)


def reorder_feeds(feed_ids: list[int]) -> None:
  """Assign sequential positions to the given feed IDs in the given order.

  Only the listed feeds are re-positioned; positions are re-assigned starting
  at the smallest existing position among them so that unrelated feeds keep
  their relative ordering.
  """
  if not feed_ids:
    return
  with db.connection() as conn, conn.cursor() as cur:
    row = cur.execute(
      "SELECT MIN(position) AS base FROM feeds WHERE id = ANY(%s)",
      (feed_ids,),
    ).fetchone()
    base = row["base"] if row and row["base"] is not None else 0
    cur.executemany(
      "UPDATE feeds SET position = %s WHERE id = %s",
      [(base + i, fid) for i, fid in enumerate(feed_ids)],
    )


def delete_feed(feed_id: int) -> None:
  with db.connection() as conn:
    conn.execute("DELETE FROM feeds WHERE id = %s", (feed_id,))


def delete_all_data() -> None:
  """Delete all feeds, articles, folders, and refresh job history."""
  with db.connection() as conn:
    conn.execute("TRUNCATE articles, feeds, folders, refresh_jobs RESTART IDENTITY CASCADE")


def toggle_feed(feed_id: int) -> None:
  """Flip `enabled`. On the true→false transition, also clear next_fetch_at
  so a later re-enable is treated as immediately due (NULL means "due now").
  The CASE uses the pre-update value of `enabled`, which is the transition
  direction.
  """
  with db.connection() as conn:
    conn.execute(
      "UPDATE feeds "
      "SET enabled = NOT enabled, "
      "    next_fetch_at = CASE WHEN enabled THEN NULL ELSE next_fetch_at END "
      "WHERE id = %s",
      (feed_id,),
    )


def rename_feed(feed_id: int, name: str) -> None:
  with db.connection() as conn:
    conn.execute("UPDATE feeds SET name = %s WHERE id = %s", (name, feed_id))


def update_feed_site_url(feed_id: int, site_url: str | None) -> None:
  with db.connection() as conn:
    conn.execute("UPDATE feeds SET site_url = %s WHERE id = %s", (site_url, feed_id))


def update_feed_extraction_rules(feed_id: int, rules: str | None) -> None:
  with db.connection() as conn:
    conn.execute(
      "UPDATE feeds SET extraction_rules = %s WHERE id = %s",
      (rules, feed_id),
    )


def update_feed_translate(feed_id: int, translate: bool) -> None:
  with db.connection() as conn:
    conn.execute(
      "UPDATE feeds SET translate = %s WHERE id = %s",
      (translate, feed_id),
    )


def get_feed_stats() -> dict[int, FeedStats]:
  """Return per-feed article statistics.

  Filters are applied post-SQL so the counts agree with `list_articles`,
  which also drops filter-matched rows. Without this the sidebar badges
  would diverge from the visible list (e.g. "39 unread" beside "All caught
  up" when every unread article is hidden by an NG-word filter).
  """
  filters = list_filters()
  with db.connection() as conn:
    if not filters:
      rows = conn.execute(
        """SELECT
             feed_id,
             COUNT(*)                                        AS article_count,
             SUM(CASE WHEN read_at IS NULL THEN 1 ELSE 0 END) AS unread_count,
             MAX(published)                                  AS latest_published,
             MIN(published)                                  AS oldest_published
           FROM articles
           WHERE feed_id IS NOT NULL
           GROUP BY feed_id""",
      ).fetchall()
      return {
        r["feed_id"]: FeedStats(
          article_count=r["article_count"],
          unread_count=r["unread_count"],
          latest_published=r["latest_published"],
          oldest_published=r["oldest_published"],
        )
        for r in rows
      }
    rows = conn.execute(
      "SELECT * FROM articles WHERE feed_id IS NOT NULL",
    ).fetchall()
  articles = [ArticleRow.model_validate(r) for r in rows]
  visible = [a for a in articles if not _article_matches_filter(a, filters)]
  by_feed: dict[int, list[ArticleRow]] = {}
  for a in visible:
    by_feed.setdefault(a.feed_id, []).append(a)  # type: ignore[arg-type]
  result: dict[int, FeedStats] = {}
  for fid, group in by_feed.items():
    pubs = [a.published for a in group if a.published is not None]
    result[fid] = FeedStats(
      article_count=len(group),
      unread_count=sum(1 for a in group if a.read_at is None),
      latest_published=max(pubs) if pubs else None,
      oldest_published=min(pubs) if pubs else None,
    )
  return result


def update_feed_fetch_status(
  feed_id: int,
  *,
  success: bool,
  error: str | None = None,
) -> None:
  """Record the outcome of a *manual* single-feed fetch.

  Intentionally touches only `last_fetched_at` and `last_error` — the adaptive
  schedule owns `consecutive_failures` / `fetch_interval_min` / `next_fetch_at`
  via record_feed_attempt, and manual trigger failures must not pollute that
  learning signal (it would inflate backoff on the next scheduled run).
  """
  with db.connection() as conn:
    if success:
      conn.execute(
        "UPDATE feeds SET last_fetched_at = NOW(), last_error = NULL "
        "WHERE id = %s",
        (feed_id,),
      )
    else:
      conn.execute(
        "UPDATE feeds SET last_error = %s WHERE id = %s",
        (error, feed_id),
      )


def toggle_summarize(feed_id: int) -> None:
  with db.connection() as conn:
    conn.execute("UPDATE feeds SET summarize = NOT summarize WHERE id = %s", (feed_id,))


# -- Articles --


_UPSERT_ARTICLE = """\
INSERT INTO articles
  (url, feed_id, title, title_translated, source, published,
   content_snippet, summary, content_html, content_translated)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (url) DO UPDATE SET
  content_html       = COALESCE(EXCLUDED.content_html,       articles.content_html),
  content_snippet    = COALESCE(EXCLUDED.content_snippet,    articles.content_snippet),
  title_translated   = COALESCE(EXCLUDED.title_translated,   articles.title_translated),
  summary            = COALESCE(EXCLUDED.summary,            articles.summary),
  content_translated = COALESCE(EXCLUDED.content_translated, articles.content_translated)
RETURNING (xmax = 0) AS inserted
"""


def upsert_articles(articles: list[Article], feed_id_map: dict[str, int] | None = None) -> int:
  """Insert new articles; update content_* on existing ones if newly available.

  Returns count of newly inserted rows (not updates).
  """
  if not articles:
    return 0
  feed_id_map = feed_id_map or {}
  new_count = 0
  with db.connection() as conn, conn.cursor() as cur:
    for a in articles:
      row = cur.execute(
        _UPSERT_ARTICLE,
        (
          a.url,
          feed_id_map.get(a.source),
          a.title,
          a.title_translated or None,
          a.source,
          a.published,
          a.content_snippet or None,
          a.summary or None,
          a.content_html or None,
          a.content_translated or None,
        ),
      ).fetchone()
      if row and row["inserted"]:
        new_count += 1
  return new_count


def list_articles(
  *,
  feed_id: int | None = None,
  unread: bool = False,
  since_days: int | None = None,
  limit: int = 10000,
) -> list[ArticleRow]:
  with db.connection() as conn:
    clauses: list[sql.Composable] = []
    params: list[object] = []
    if feed_id is not None:
      clauses.append(sql.SQL("feed_id = %s"))
      params.append(feed_id)
    if unread:
      clauses.append(sql.SQL("read_at IS NULL"))
    if since_days is not None:
      clauses.append(sql.SQL(
        "COALESCE(published, fetched_at) >= NOW() - %s * INTERVAL '1 day'"
      ))
      params.append(since_days)
    where = sql.SQL("WHERE ") + sql.SQL(" AND ").join(clauses) if clauses else sql.SQL("")
    filters = list_filters()
    # Over-fetch when filters are active so post-SQL filtering still yields
    # roughly `limit` results.
    fetch_limit = limit * 3 if filters else limit
    params.append(fetch_limit)
    # SQL returns newest first so LIMIT keeps the most recent `limit` rows
    # even when total >> limit. Final response is reversed to oldest-first so
    # the UI can render in ascending publication order without re-sorting.
    rows = conn.execute(
      sql.SQL(
        "SELECT * FROM articles {where} "
        "ORDER BY COALESCE(published, fetched_at) DESC NULLS LAST "
        "LIMIT %s"
      ).format(where=where),
      params,
    ).fetchall()
    articles = [ArticleRow.model_validate(r) for r in rows]
    if filters:
      articles = [a for a in articles if not _article_matches_filter(a, filters)]
    return list(reversed(articles[:limit]))


def list_pending_summaries(limit: int = 5) -> list[ArticleRow]:
  """Find articles needing summarization.

  Condition must match `fetcher._needs_llm`:
    - translate=TRUE: title_translated missing, or both summary and content_translated missing.
    - translate=FALSE: summary missing AND content is long enough to summarize (>= 300 chars).
  """
  with db.connection() as conn:
    rows = conn.execute(
      """SELECT a.* FROM articles a
         JOIN feeds f ON a.feed_id = f.id
         WHERE f.summarize = TRUE
           AND (
             (f.translate AND (
               a.title_translated IS NULL
               OR (a.summary IS NULL AND a.content_translated IS NULL)
             ))
             OR
             (NOT f.translate
               AND a.summary IS NULL
               AND LENGTH(COALESCE(a.content_snippet, '')) >= 300)
           )
         ORDER BY a.published ASC
         LIMIT %s""",
      (limit,),
    ).fetchall()
    return [ArticleRow.model_validate(r) for r in rows]


def update_article_summary(
  url: str,
  title_translated: str | None,
  summary: str | None,
  content_translated: str | None = None,
) -> None:
  with db.connection() as conn:
    conn.execute(
      """UPDATE articles
         SET title_translated   = COALESCE(%s, title_translated),
             summary            = COALESCE(%s, summary),
             content_translated = COALESCE(%s, content_translated)
         WHERE url = %s""",
      (title_translated, summary, content_translated, url),
    )


def mark_read(urls: list[str]) -> None:
  if not urls:
    return
  with db.connection() as conn:
    conn.execute(
      "UPDATE articles SET read_at = NOW() WHERE url = ANY(%s) AND read_at IS NULL",
      (urls,),
    )


def mark_unread(urls: list[str]) -> None:
  if not urls:
    return
  with db.connection() as conn:
    conn.execute("UPDATE articles SET read_at = NULL WHERE url = ANY(%s)", (urls,))


def mark_all_unread(feed_id: int | None = None) -> int:
  clauses: list[sql.Composable] = [sql.SQL("read_at IS NOT NULL")]
  params: list[Any] = []
  if feed_id is not None:
    clauses.append(sql.SQL("feed_id = %s"))
    params.append(feed_id)
  query = sql.SQL("UPDATE articles SET read_at = NULL WHERE {}").format(
    sql.SQL(" AND ").join(clauses),
  )
  with db.connection() as conn:
    cur = conn.execute(query, params)
    return cur.rowcount


def mark_all_read(feed_id: int | None = None, older_than_hours: int | None = None) -> int:
  # Age is measured against COALESCE(published, fetched_at) so feeds without a
  # `published` value still age out based on when we first saw them.
  clauses: list[sql.Composable] = [sql.SQL("read_at IS NULL")]
  params: list[Any] = []
  if feed_id is not None:
    clauses.append(sql.SQL("feed_id = %s"))
    params.append(feed_id)
  if older_than_hours is not None and older_than_hours > 0:
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=older_than_hours)
    clauses.append(sql.SQL("COALESCE(published, fetched_at) < %s"))
    params.append(cutoff)
  query = sql.SQL("UPDATE articles SET read_at = NOW() WHERE {}").format(
    sql.SQL(" AND ").join(clauses),
  )
  with db.connection() as conn:
    cur = conn.execute(query, params)
    return cur.rowcount


def prune_articles(max_age_days: int) -> int:
  # 0 (or negative) disables pruning — articles are retained indefinitely.
  if max_age_days <= 0:
    return 0
  cutoff = datetime.now(tz=timezone.utc) - timedelta(days=max_age_days)
  with db.connection() as conn:
    cur = conn.execute("DELETE FROM articles WHERE fetched_at < %s", (cutoff,))
    return cur.rowcount


# -- Refresh jobs --


def create_refresh_job(source: str) -> int:
  with db.connection() as conn:
    row = conn.execute(
      "INSERT INTO refresh_jobs (source, status) VALUES (%s, 'running') RETURNING id",
      (source,),
    ).fetchone()
    return row["id"] if row else 0


def finish_refresh_job(
  job_id: int,
  status: str,
  new_count: int,
  error: str | None,
) -> None:
  with db.connection() as conn:
    conn.execute(
      """UPDATE refresh_jobs
         SET finished_at = NOW(), status = %s, new_count = %s, error = %s
         WHERE id = %s""",
      (status, new_count, error, job_id),
    )


def get_latest_refresh_job() -> RefreshJob | None:
  with db.connection() as conn:
    row = conn.execute(
      "SELECT * FROM refresh_jobs ORDER BY id DESC LIMIT 1",
    ).fetchone()
    return RefreshJob.model_validate(row) if row else None
