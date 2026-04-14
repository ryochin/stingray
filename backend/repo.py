"""Repository functions for feeds, articles, and refresh jobs."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from models import Article
from schemas import ArticleRow, FeedRow, FolderRow, NgWordRow, RefreshJob

import db


def _now_iso() -> str:
  return datetime.now(tz=timezone.utc).isoformat()


# -- NG words --


def list_ng_words() -> list[NgWordRow]:
  conn = db.get_conn()
  try:
    rows = conn.execute("SELECT * FROM ng_words ORDER BY id").fetchall()
    return [
      NgWordRow(
        id=r["id"],
        pattern=r["pattern"],
        target=r["target"],
        created_at=datetime.fromisoformat(r["created_at"]),
      )
      for r in rows
    ]
  finally:
    conn.close()


def add_ng_word(pattern: str, target: str = "title") -> int:
  conn = db.get_conn()
  try:
    cur = conn.execute(
      "INSERT INTO ng_words (pattern, target, created_at) VALUES (?, ?, ?)",
      (pattern, target, _now_iso()),
    )
    conn.commit()
    return cur.lastrowid or 0
  finally:
    conn.close()


def delete_ng_word(ng_word_id: int) -> None:
  conn = db.get_conn()
  try:
    conn.execute("DELETE FROM ng_words WHERE id = ?", (ng_word_id,))
    conn.commit()
  finally:
    conn.close()


def _parse_ng_pattern(pattern: str) -> tuple[bool, str]:
  if len(pattern) > 2 and pattern.startswith("/") and pattern.endswith("/"):
    return True, pattern[1:-1]
  return False, pattern


def _article_matches_ng(article: ArticleRow, ng_words: list[NgWordRow]) -> bool:
  for ng in ng_words:
    is_regex, pat = _parse_ng_pattern(ng.pattern)
    if ng.target == "title":
      texts = [article.title, article.title_ja or ""]
    else:
      texts = [article.title, article.title_ja or "",
               article.content_snippet or "", article.summary or ""]
    for text in texts:
      if not text:
        continue
      if is_regex:
        try:
          if re.search(pat, text, re.IGNORECASE):
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
        folder_id=r["folder_id"],
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
      """INSERT INTO feeds (name, type, url, subreddit, sort, lang, max_items, summarize, enabled, folder_id, created_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
        feed.folder_id,
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
               WHERE url = ? AND (title_ja IS NULL AND summary IS NULL)""",
            (a.title_ja or None, a.summary or None, a.url),
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
    params.append(limit)
    rows = conn.execute(
      f"SELECT * FROM articles {where} ORDER BY published ASC LIMIT ?",
      params,
    ).fetchall()
    articles = [_row_to_article(r) for r in rows]
    ng_words = list_ng_words()
    if ng_words:
      articles = [a for a in articles if not _article_matches_ng(a, ng_words)]
    return articles
  finally:
    conn.close()


def _row_to_article(r: object) -> ArticleRow:
  """Convert a sqlite3.Row to an ArticleRow."""
  return ArticleRow(
    url=r["url"],  # type: ignore[index]
    feed_id=r["feed_id"],  # type: ignore[index]
    title=r["title"],  # type: ignore[index]
    title_ja=r["title_ja"],  # type: ignore[index]
    source=r["source"],  # type: ignore[index]
    published=datetime.fromisoformat(r["published"]) if r["published"] else None,  # type: ignore[index]
    content_snippet=r["content_snippet"],  # type: ignore[index]
    summary=r["summary"],  # type: ignore[index]
    lang=r["lang"],  # type: ignore[index]
    fetched_at=datetime.fromisoformat(r["fetched_at"]),  # type: ignore[index]
    read_at=datetime.fromisoformat(r["read_at"]) if r["read_at"] else None,  # type: ignore[index]
  )


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
