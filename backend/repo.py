"""Repository functions for feeds, articles, and refresh jobs."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from psycopg import sql

from models import Article
from schemas import ArticleRow, FeedRow, FeedStats, FilterRow, FolderRow, RefreshJob

import db


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
  """Delete all feeds, articles, and folders."""
  with db.connection() as conn:
    conn.execute("TRUNCATE articles, feeds, folders RESTART IDENTITY CASCADE")


def toggle_feed(feed_id: int) -> None:
  with db.connection() as conn:
    conn.execute("UPDATE feeds SET enabled = NOT enabled WHERE id = %s", (feed_id,))


def rename_feed(feed_id: int, name: str) -> None:
  with db.connection() as conn:
    conn.execute("UPDATE feeds SET name = %s WHERE id = %s", (name, feed_id))


def update_feed_site_url(feed_id: int, site_url: str) -> None:
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
  """Return per-feed article statistics."""
  with db.connection() as conn:
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


def update_feed_fetch_status(
  feed_id: int,
  *,
  success: bool,
  error: str | None = None,
) -> None:
  with db.connection() as conn:
    if success:
      conn.execute(
        "UPDATE feeds SET last_fetched_at = NOW(), consecutive_failures = 0, last_error = NULL WHERE id = %s",
        (feed_id,),
      )
    else:
      conn.execute(
        "UPDATE feeds SET consecutive_failures = consecutive_failures + 1, last_error = %s WHERE id = %s",
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
  limit: int = 500,
) -> list[ArticleRow]:
  with db.connection() as conn:
    clauses: list[sql.Composable] = []
    params: list[object] = []
    if feed_id is not None:
      clauses.append(sql.SQL("feed_id = %s"))
      params.append(feed_id)
    if unread:
      clauses.append(sql.SQL("read_at IS NULL"))
    where = sql.SQL("WHERE ") + sql.SQL(" AND ").join(clauses) if clauses else sql.SQL("")
    filters = list_filters()
    # Over-fetch when filters are active so post-SQL filtering still yields
    # roughly `limit` results.
    fetch_limit = limit * 3 if filters else limit
    params.append(fetch_limit)
    rows = conn.execute(
      sql.SQL("SELECT * FROM articles {where} ORDER BY published ASC LIMIT %s").format(where=where),
      params,
    ).fetchall()
    articles = [ArticleRow.model_validate(r) for r in rows]
    if filters:
      articles = [a for a in articles if not _article_matches_filter(a, filters)]
    return articles[:limit]


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


def mark_all_read(feed_id: int | None = None) -> int:
  with db.connection() as conn:
    if feed_id is not None:
      cur = conn.execute(
        "UPDATE articles SET read_at = NOW() WHERE feed_id = %s AND read_at IS NULL",
        (feed_id,),
      )
    else:
      cur = conn.execute("UPDATE articles SET read_at = NOW() WHERE read_at IS NULL")
    return cur.rowcount


def prune_articles(max_age_days: int) -> int:
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
