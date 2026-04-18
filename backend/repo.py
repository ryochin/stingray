"""Repository functions for feeds, articles, and refresh jobs."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from models import Article
from schemas import ArticleRow, FeedRow, FeedStats, FilterRow, FolderRow, RefreshJob

import db


def _now_iso() -> str:
  return datetime.now(tz=timezone.utc).isoformat()


# -- Filters --


def list_filters() -> list[FilterRow]:
  conn = db.get_conn()
  try:
    rows = conn.execute("SELECT * FROM filters ORDER BY id").fetchall()
    return [
      FilterRow(
        id=r["id"],
        pattern=r["pattern"],
        target=r["target"],
        created_at=datetime.fromisoformat(r["created_at"]),
      )
      for r in rows
    ]
  finally:
    conn.close()


def add_filter(pattern: str, target: str = "title") -> int:
  conn = db.get_conn()
  try:
    cur = conn.execute(
      "INSERT INTO filters (pattern, target, created_at) VALUES (?, ?, ?)",
      (pattern, target, _now_iso()),
    )
    conn.commit()
    return cur.lastrowid or 0
  finally:
    conn.close()


def delete_filter(filter_id: int) -> None:
  conn = db.get_conn()
  try:
    conn.execute("DELETE FROM filters WHERE id = ?", (filter_id,))
    conn.commit()
  finally:
    conn.close()


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
  conn = db.get_conn()
  try:
    rows = conn.execute("SELECT * FROM folders ORDER BY position, id").fetchall()
    return [
      FolderRow(
        id=r["id"],
        name=r["name"],
        position=r["position"],
        created_at=datetime.fromisoformat(r["created_at"]),
      )
      for r in rows
    ]
  finally:
    conn.close()


def create_folder(name: str) -> int:
  conn = db.get_conn()
  try:
    row = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM folders").fetchone()
    pos = row[0]
    cur = conn.execute(
      "INSERT INTO folders (name, position, created_at) VALUES (?, ?, ?)",
      (name, pos, _now_iso()),
    )
    conn.commit()
    return cur.lastrowid or 0
  finally:
    conn.close()


def rename_folder(folder_id: int, name: str) -> None:
  conn = db.get_conn()
  try:
    conn.execute("UPDATE folders SET name = ? WHERE id = ?", (name, folder_id))
    conn.commit()
  finally:
    conn.close()


def delete_folder(folder_id: int) -> None:
  conn = db.get_conn()
  try:
    conn.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
    conn.commit()
  finally:
    conn.close()


def reorder_folders(folder_ids: list[int]) -> None:
  conn = db.get_conn()
  try:
    conn.executemany(
      "UPDATE folders SET position = ? WHERE id = ?",
      [(i, fid) for i, fid in enumerate(folder_ids)],
    )
    conn.commit()
  finally:
    conn.close()


# -- Feeds --


def move_feed_to_folder(feed_id: int, folder_id: int | None) -> None:
  conn = db.get_conn()
  try:
    conn.execute("UPDATE feeds SET folder_id = ? WHERE id = ?", (folder_id, feed_id))
    conn.commit()
  finally:
    conn.close()


def _row_to_feed(r: object) -> FeedRow:
  return FeedRow(
    id=r["id"],  # type: ignore[index]
    name=r["name"],  # type: ignore[index]
    url=r["url"],  # type: ignore[index]
    site_url=r["site_url"],  # type: ignore[index]
    translate=bool(r["translate"]),  # type: ignore[index]
    max_items=r["max_items"],  # type: ignore[index]
    summarize=bool(r["summarize"]),  # type: ignore[index]
    enabled=bool(r["enabled"]),  # type: ignore[index]
    folder_id=r["folder_id"],  # type: ignore[index]
    position=r["position"],  # type: ignore[index]
    last_fetched_at=datetime.fromisoformat(r["last_fetched_at"]) if r["last_fetched_at"] else None,  # type: ignore[index]
    consecutive_failures=r["consecutive_failures"],  # type: ignore[index]
    last_error=r["last_error"],  # type: ignore[index]
    extraction_rules=r["extraction_rules"],  # type: ignore[index]
    created_at=datetime.fromisoformat(r["created_at"]),  # type: ignore[index]
  )


_FEED_ORDER = "ORDER BY position, id"


def list_feeds(*, enabled: bool | None = None) -> list[FeedRow]:
  conn = db.get_conn()
  try:
    if enabled is None:
      rows = conn.execute(f"SELECT * FROM feeds {_FEED_ORDER}").fetchall()
    else:
      rows = conn.execute(
        f"SELECT * FROM feeds WHERE enabled = ? {_FEED_ORDER}",
        (int(enabled),),
      ).fetchall()
    return [_row_to_feed(r) for r in rows]
  finally:
    conn.close()


def get_feed_by_id(feed_id: int) -> FeedRow | None:
  conn = db.get_conn()
  try:
    row = conn.execute("SELECT * FROM feeds WHERE id = ?", (feed_id,)).fetchone()
    return _row_to_feed(row) if row else None
  finally:
    conn.close()


def add_feed(feed: FeedRow) -> int:
  conn = db.get_conn()
  try:
    # New feeds go to the end of the list.
    row = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM feeds").fetchone()
    pos = row[0]
    cur = conn.execute(
      """INSERT INTO feeds (name, url, site_url, translate, max_items, summarize, enabled, folder_id, position, extraction_rules, created_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
      (
        feed.name,
        feed.url,
        feed.site_url,
        int(feed.translate),
        feed.max_items,
        int(feed.summarize),
        int(feed.enabled),
        feed.folder_id,
        pos,
        feed.extraction_rules,
        _now_iso(),
      ),
    )
    conn.commit()
    return cur.lastrowid or 0
  finally:
    conn.close()


def reorder_feeds(feed_ids: list[int]) -> None:
  """Assign sequential positions to the given feed IDs in the given order.

  Only the listed feeds are re-positioned; positions are re-assigned starting
  at the smallest existing position among them so that unrelated feeds keep
  their relative ordering.
  """
  if not feed_ids:
    return
  conn = db.get_conn()
  try:
    placeholders = ",".join("?" * len(feed_ids))
    row = conn.execute(
      f"SELECT MIN(position) FROM feeds WHERE id IN ({placeholders})",
      feed_ids,
    ).fetchone()
    base = row[0] if row and row[0] is not None else 0
    conn.executemany(
      "UPDATE feeds SET position = ? WHERE id = ?",
      [(base + i, fid) for i, fid in enumerate(feed_ids)],
    )
    conn.commit()
  finally:
    conn.close()


def delete_feed(feed_id: int) -> None:
  conn = db.get_conn()
  try:
    conn.execute("DELETE FROM feeds WHERE id = ?", (feed_id,))
    conn.commit()
  finally:
    conn.close()


def delete_all_data() -> None:
  """Delete all feeds, articles, and folders."""
  conn = db.get_conn()
  try:
    conn.execute("DELETE FROM articles")
    conn.execute("DELETE FROM feeds")
    conn.execute("DELETE FROM folders")
    conn.commit()
  finally:
    conn.close()


def toggle_feed(feed_id: int) -> None:
  conn = db.get_conn()
  try:
    conn.execute(
      "UPDATE feeds SET enabled = NOT enabled WHERE id = ?",
      (feed_id,),
    )
    conn.commit()
  finally:
    conn.close()


def rename_feed(feed_id: int, name: str) -> None:
  conn = db.get_conn()
  try:
    conn.execute(
      "UPDATE feeds SET name = ? WHERE id = ?",
      (name, feed_id),
    )
    conn.commit()
  finally:
    conn.close()


def update_feed_site_url(feed_id: int, site_url: str) -> None:
  conn = db.get_conn()
  try:
    conn.execute(
      "UPDATE feeds SET site_url = ? WHERE id = ?",
      (site_url, feed_id),
    )
    conn.commit()
  finally:
    conn.close()


def update_feed_extraction_rules(feed_id: int, rules: str | None) -> None:
  conn = db.get_conn()
  try:
    conn.execute(
      "UPDATE feeds SET extraction_rules = ? WHERE id = ?",
      (rules, feed_id),
    )
    conn.commit()
  finally:
    conn.close()


def update_feed_translate(feed_id: int, translate: bool) -> None:
  conn = db.get_conn()
  try:
    conn.execute(
      "UPDATE feeds SET translate = ? WHERE id = ?",
      (int(translate), feed_id),
    )
    conn.commit()
  finally:
    conn.close()


def get_feed_stats() -> dict[int, FeedStats]:
  """Return per-feed article statistics."""
  conn = db.get_conn()
  try:
    rows = conn.execute(
      """SELECT
           feed_id,
           COUNT(*) AS article_count,
           SUM(CASE WHEN read_at IS NULL THEN 1 ELSE 0 END) AS unread_count,
           MAX(published) AS latest_published,
           MIN(published) AS oldest_published
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
  finally:
    conn.close()


def update_feed_fetch_status(
  feed_id: int,
  *,
  success: bool,
  error: str | None = None,
) -> None:
  conn = db.get_conn()
  try:
    now = _now_iso()
    if success:
      conn.execute(
        "UPDATE feeds SET last_fetched_at = ?, consecutive_failures = 0, last_error = NULL WHERE id = ?",
        (now, feed_id),
      )
    else:
      conn.execute(
        "UPDATE feeds SET consecutive_failures = consecutive_failures + 1, last_error = ? WHERE id = ?",
        (error, feed_id),
      )
    conn.commit()
  finally:
    conn.close()


def toggle_summarize(feed_id: int) -> None:
  conn = db.get_conn()
  try:
    conn.execute(
      "UPDATE feeds SET summarize = NOT summarize WHERE id = ?",
      (feed_id,),
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
           (url, feed_id, title, title_translated, source, published, content_snippet, summary, content_html, content_translated, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
          a.url,
          feed_id_map.get(a.source),
          a.title,
          a.title_translated or None,
          a.source,
          a.published.isoformat() if a.published else None,
          a.content_snippet or None,
          a.summary or None,
          a.content_html or None,
          a.content_translated or None,
          now,
        ),
      )
      if cur.rowcount > 0:
        new_count += 1
      else:
        # Update existing articles: content_html always, title_translated/summary only if missing
        updates = []
        params: list[object] = []
        if a.content_html:
          updates.append("content_html = ?")
          params.append(a.content_html)
        if a.content_snippet:
          updates.append("content_snippet = COALESCE(?, content_snippet)")
          params.append(a.content_snippet)
        if a.title_translated:
          updates.append("title_translated = COALESCE(?, title_translated)")
          params.append(a.title_translated)
        if a.summary:
          updates.append("summary = COALESCE(?, summary)")
          params.append(a.summary)
        if a.content_translated:
          updates.append("content_translated = COALESCE(?, content_translated)")
          params.append(a.content_translated)
        if updates:
          params.append(a.url)
          conn.execute(
            f"UPDATE articles SET {', '.join(updates)} WHERE url = ?",
            params,
          )
    conn.commit()
    return new_count
  finally:
    conn.close()


def list_articles(
  *,
  feed_id: int | None = None,
  unread: bool = False,
  limit: int = 500,
) -> list[ArticleRow]:
  conn = db.get_conn()
  try:
    clauses: list[str] = []
    params: list[object] = []
    if feed_id is not None:
      clauses.append("feed_id = ?")
      params.append(feed_id)
    if unread:
      clauses.append("read_at IS NULL")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    filters = list_filters()
    # Over-fetch when filters are active so post-SQL filtering still yields
    # roughly `limit` results. Moving LIKE/REGEXP into SQL would be complex;
    # `* 3` gives more headroom than the old `* 2` at negligible cost.
    fetch_limit = limit * 3 if filters else limit
    params.append(fetch_limit)
    rows = conn.execute(
      f"SELECT * FROM articles {where} ORDER BY published ASC LIMIT ?",
      params,
    ).fetchall()
    articles = [_row_to_article(r) for r in rows]
    if filters:
      articles = [a for a in articles if not _article_matches_filter(a, filters)]
    return articles[:limit]
  finally:
    conn.close()


def _row_to_article(r: object) -> ArticleRow:
  """Convert a sqlite3.Row to an ArticleRow."""
  return ArticleRow(
    url=r["url"],  # type: ignore[index]
    feed_id=r["feed_id"],  # type: ignore[index]
    title=r["title"],  # type: ignore[index]
    title_translated=r["title_translated"],  # type: ignore[index]
    source=r["source"],  # type: ignore[index]
    published=datetime.fromisoformat(r["published"]) if r["published"] else None,  # type: ignore[index]
    content_snippet=r["content_snippet"],  # type: ignore[index]
    summary=r["summary"],  # type: ignore[index]
    content_html=r["content_html"],  # type: ignore[index]
    content_translated=r["content_translated"],  # type: ignore[index]
    fetched_at=datetime.fromisoformat(r["fetched_at"]),  # type: ignore[index]
    read_at=datetime.fromisoformat(r["read_at"]) if r["read_at"] else None,  # type: ignore[index]
  )


def list_pending_summaries(limit: int = 5) -> list[ArticleRow]:
  """Find articles needing summarization (feed.summarize=1, missing summary)."""
  conn = db.get_conn()
  try:
    rows = conn.execute(
      """SELECT a.* FROM articles a
         JOIN feeds f ON a.feed_id = f.id
         WHERE f.summarize = 1
           AND (a.summary IS NULL OR (f.translate = 1 AND a.title_translated IS NULL))
         ORDER BY a.published ASC
         LIMIT ?""",
      (limit,),
    ).fetchall()
    return [_row_to_article(r) for r in rows]
  finally:
    conn.close()


def update_article_summary(
  url: str,
  title_translated: str | None,
  summary: str | None,
  content_translated: str | None = None,
) -> None:
  conn = db.get_conn()
  try:
    conn.execute(
      """UPDATE articles
         SET title_translated = COALESCE(?, title_translated),
             summary = COALESCE(?, summary),
             content_translated = COALESCE(?, content_translated)
         WHERE url = ?""",
      (title_translated, summary, content_translated, url),
    )
    conn.commit()
  finally:
    conn.close()


def mark_read(urls: list[str]) -> None:
  if not urls:
    return
  conn = db.get_conn()
  try:
    now = _now_iso()
    conn.executemany(
      "UPDATE articles SET read_at = ? WHERE url = ? AND read_at IS NULL",
      [(now, url) for url in urls],
    )
    conn.commit()
  finally:
    conn.close()


def mark_unread(urls: list[str]) -> None:
  if not urls:
    return
  conn = db.get_conn()
  try:
    conn.executemany(
      "UPDATE articles SET read_at = NULL WHERE url = ?",
      [(url,) for url in urls],
    )
    conn.commit()
  finally:
    conn.close()


def mark_all_read(feed_id: int | None = None) -> int:
  conn = db.get_conn()
  try:
    now = _now_iso()
    if feed_id is not None:
      cur = conn.execute(
        "UPDATE articles SET read_at = ? WHERE feed_id = ? AND read_at IS NULL",
        (now, feed_id),
      )
    else:
      cur = conn.execute(
        "UPDATE articles SET read_at = ? WHERE read_at IS NULL",
        (now,),
      )
    conn.commit()
    return cur.rowcount
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
