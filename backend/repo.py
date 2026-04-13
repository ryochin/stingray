"""Repository functions for feeds, articles, and refresh jobs."""

from __future__ import annotations

from datetime import datetime, timezone

from models import Article
from schemas import ArticleRow, FeedRow, RefreshJob

import db


def _now_iso() -> str:
  return datetime.now(tz=timezone.utc).isoformat()


# -- Feeds --


def list_feeds(*, enabled: bool | None = None) -> list[FeedRow]:
  conn = db.get_conn()
  try:
    if enabled is None:
      rows = conn.execute("SELECT * FROM feeds ORDER BY id").fetchall()
    else:
      rows = conn.execute(
        "SELECT * FROM feeds WHERE enabled = ? ORDER BY id",
        (int(enabled),),
      ).fetchall()
    return [
      FeedRow(
        id=r["id"],
        name=r["name"],
        type=r["type"],
        url=r["url"],
        subreddit=r["subreddit"],
        sort=r["sort"],
        lang=r["lang"],
        max_items=r["max_items"],
        summarize=bool(r["summarize"]),
        enabled=bool(r["enabled"]),
        created_at=datetime.fromisoformat(r["created_at"]),
      )
      for r in rows
    ]
  finally:
    conn.close()


def add_feed(feed: FeedRow) -> int:
  conn = db.get_conn()
  try:
    cur = conn.execute(
      """INSERT INTO feeds (name, type, url, subreddit, sort, lang, max_items, summarize, enabled, created_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
      (
        feed.name,
        feed.type,
        feed.url,
        feed.subreddit,
        feed.sort,
        feed.lang,
        feed.max_items,
        int(feed.summarize),
        int(feed.enabled),
        _now_iso(),
      ),
    )
    conn.commit()
    return cur.lastrowid or 0
  finally:
    conn.close()


def delete_feed(feed_id: int) -> None:
  conn = db.get_conn()
  try:
    conn.execute("DELETE FROM feeds WHERE id = ?", (feed_id,))
    conn.commit()
  finally:
    conn.close()


def toggle_feed(feed_id: int, enabled: bool) -> None:
  conn = db.get_conn()
  try:
    conn.execute(
      "UPDATE feeds SET enabled = ? WHERE id = ?",
      (int(enabled), feed_id),
    )
    conn.commit()
  finally:
    conn.close()


def set_summarize(feed_id: int, on: bool) -> None:
  conn = db.get_conn()
  try:
    conn.execute(
      "UPDATE feeds SET summarize = ? WHERE id = ?",
      (int(on), feed_id),
    )
    conn.commit()
  finally:
    conn.close()


# -- Articles --


def upsert_articles(articles: list[Article], feed_id_map: dict[str, int] | None = None) -> int:
  """Insert new articles, skip existing ones (INSERT OR IGNORE).

  feed_id_map: mapping of source name -> feed id, used to set feed_id.
  Returns the number of newly inserted articles.
  """
  if not articles:
    return 0
  feed_id_map = feed_id_map or {}
  now = _now_iso()
  conn = db.get_conn()
  try:
    new_count = 0
    for a in articles:
      cur = conn.execute(
        """INSERT OR IGNORE INTO articles
           (url, feed_id, title, title_ja, source, published, content_snippet, summary, lang, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
          a.url,
          feed_id_map.get(a.source),
          a.title,
          a.title_ja or None,
          a.source,
          a.published.isoformat() if a.published else None,
          a.content_snippet or None,
          a.summary or None,
          a.lang,
          now,
        ),
      )
      if cur.rowcount > 0:
        new_count += 1
      else:
        # Update title_ja and summary for existing articles if they were enriched
        if a.title_ja or a.summary:
          conn.execute(
            """UPDATE articles SET
                 title_ja = COALESCE(?, title_ja),
                 summary = COALESCE(?, summary)
               WHERE url = ? AND (title_ja IS NULL OR summary IS NULL)""",
            (a.title_ja or None, a.summary or None, a.url),
          )
    conn.commit()
    return new_count
  finally:
    conn.close()


def list_articles(
  *,
  feed_id: int | None = None,
  limit: int = 500,
) -> list[ArticleRow]:
  conn = db.get_conn()
  try:
    if feed_id is not None:
      rows = conn.execute(
        "SELECT * FROM articles WHERE feed_id = ? ORDER BY published DESC LIMIT ?",
        (feed_id, limit),
      ).fetchall()
    else:
      rows = conn.execute(
        "SELECT * FROM articles ORDER BY published DESC LIMIT ?",
        (limit,),
      ).fetchall()
    return [
      ArticleRow(
        url=r["url"],
        feed_id=r["feed_id"],
        title=r["title"],
        title_ja=r["title_ja"],
        source=r["source"],
        published=datetime.fromisoformat(r["published"]) if r["published"] else None,
        content_snippet=r["content_snippet"],
        summary=r["summary"],
        lang=r["lang"],
        fetched_at=datetime.fromisoformat(r["fetched_at"]),
      )
      for r in rows
    ]
  finally:
    conn.close()


def prune_articles(max_age_days: int) -> int:
  from datetime import timedelta
  cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=max_age_days)).isoformat()
  conn = db.get_conn()
  try:
    cur = conn.execute(
      "DELETE FROM articles WHERE fetched_at < ?",
      (cutoff,),
    )
    conn.commit()
    return cur.rowcount
  finally:
    conn.close()


# -- Refresh jobs --


def create_refresh_job(source: str) -> int:
  conn = db.get_conn()
  try:
    cur = conn.execute(
      "INSERT INTO refresh_jobs (started_at, source, status) VALUES (?, ?, 'running')",
      (_now_iso(), source),
    )
    conn.commit()
    return cur.lastrowid or 0
  finally:
    conn.close()


def finish_refresh_job(
  job_id: int,
  status: str,
  new_count: int,
  error: str | None,
) -> None:
  conn = db.get_conn()
  try:
    conn.execute(
      """UPDATE refresh_jobs
         SET finished_at = ?, status = ?, new_count = ?, error = ?
         WHERE id = ?""",
      (_now_iso(), status, new_count, error, job_id),
    )
    conn.commit()
  finally:
    conn.close()


def get_latest_refresh_job() -> RefreshJob | None:
  conn = db.get_conn()
  try:
    row = conn.execute(
      "SELECT * FROM refresh_jobs ORDER BY id DESC LIMIT 1",
    ).fetchone()
    if row is None:
      return None
    return RefreshJob(
      id=row["id"],
      started_at=datetime.fromisoformat(row["started_at"]),
      finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
      source=row["source"],
      status=row["status"],
      new_count=row["new_count"],
      error=row["error"],
    )
  finally:
    conn.close()
