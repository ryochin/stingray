"""One-shot migration: copy rows from an existing SQLite news.db into Postgres.

Usage:
  DATABASE_URL=postgresql://news:news@localhost:25432/news \\
    uv run python backend/migrate_sqlite_to_pg.py data/news.db
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import db


def _parse_ts(s: str | None) -> datetime | None:
  if not s:
    return None
  try:
    return datetime.fromisoformat(s)
  except ValueError:
    return None


def _bool(v: object) -> bool:
  return bool(v)


def migrate(sqlite_path: Path) -> None:
  src = sqlite3.connect(str(sqlite_path))
  src.row_factory = sqlite3.Row

  db.configure()
  db.init_schema()

  with db.connection() as conn, conn.cursor() as cur:
    # Wipe destination first so the script is idempotent.
    cur.execute("TRUNCATE articles, feeds, folders, filters, refresh_jobs RESTART IDENTITY CASCADE")

    # folders
    folders = src.execute("SELECT * FROM folders ORDER BY id").fetchall()
    id_map_folder: dict[int, int] = {}
    for f in folders:
      row = cur.execute(
        """INSERT INTO folders (name, position, created_at)
           VALUES (%s, %s, %s) RETURNING id""",
        (f["name"], f["position"], _parse_ts(f["created_at"])),
      ).fetchone()
      if row:
        id_map_folder[f["id"]] = row["id"]
    print(f"folders: {len(folders)}")

    # feeds
    feeds = src.execute("SELECT * FROM feeds ORDER BY id").fetchall()
    id_map_feed: dict[int, int] = {}
    feed_cols = {c[1] for c in src.execute("PRAGMA table_info(feeds)").fetchall()}
    for f in feeds:
      folder_id = id_map_folder.get(f["folder_id"]) if f["folder_id"] is not None else None
      row = cur.execute(
        """INSERT INTO feeds
             (name, url, site_url, translate, summarize, enabled,
              folder_id, position, last_fetched_at, consecutive_failures,
              last_error, extraction_rules, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING id""",
        (
          f["name"],
          f["url"],
          f["site_url"] if "site_url" in feed_cols else None,
          _bool(f["translate"]) if "translate" in feed_cols else False,
          _bool(f["summarize"]),
          _bool(f["enabled"]),
          folder_id,
          f["position"] if "position" in feed_cols else 0,
          _parse_ts(f["last_fetched_at"]) if "last_fetched_at" in feed_cols else None,
          f["consecutive_failures"] if "consecutive_failures" in feed_cols else 0,
          f["last_error"] if "last_error" in feed_cols else None,
          f["extraction_rules"] if "extraction_rules" in feed_cols else None,
          _parse_ts(f["created_at"]),
        ),
      ).fetchone()
      if row:
        id_map_feed[f["id"]] = row["id"]
    print(f"feeds: {len(feeds)}")

    # articles
    articles = src.execute("SELECT * FROM articles").fetchall()
    art_cols = {c[1] for c in src.execute("PRAGMA table_info(articles)").fetchall()}
    for a in articles:
      feed_id = id_map_feed.get(a["feed_id"]) if a["feed_id"] is not None else None
      # Old DBs stored translated titles only in `title_ja`; fall back to that
      # column when the newer `title_translated` is missing or NULL so we don't
      # drop translations during the one-shot migration.
      title_translated = None
      if "title_translated" in art_cols and a["title_translated"]:
        title_translated = a["title_translated"]
      elif "title_ja" in art_cols and a["title_ja"]:
        title_translated = a["title_ja"]
      cur.execute(
        """INSERT INTO articles
             (url, feed_id, title, title_translated, source, published,
              content_snippet, summary, content_html, content_translated,
              fetched_at, read_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (url) DO NOTHING""",
        (
          a["url"],
          feed_id,
          a["title"],
          title_translated,
          a["source"],
          _parse_ts(a["published"]),
          a["content_snippet"],
          a["summary"],
          a["content_html"] if "content_html" in art_cols else None,
          a["content_translated"] if "content_translated" in art_cols else None,
          _parse_ts(a["fetched_at"]) or datetime.now(),
          _parse_ts(a["read_at"]) if "read_at" in art_cols else None,
        ),
      )
    print(f"articles: {len(articles)}")

    # filters
    filters = src.execute("SELECT * FROM filters").fetchall()
    for f in filters:
      cur.execute(
        """INSERT INTO filters (pattern, target, created_at)
           VALUES (%s, %s, %s)""",
        (f["pattern"], f["target"], _parse_ts(f["created_at"])),
      )
    print(f"filters: {len(filters)}")

    # Bump identity sequences past max(id) so future inserts don't collide.
    for tbl in ("folders", "feeds", "filters", "refresh_jobs"):
      cur.execute(f"""
        SELECT setval(
          pg_get_serial_sequence('{tbl}', 'id'),
          COALESCE((SELECT MAX(id) FROM {tbl}), 0) + 1,
          false
        )
      """)

  src.close()
  print("Done.")


if __name__ == "__main__":
  if len(sys.argv) != 2:
    print("Usage: migrate_sqlite_to_pg.py <path/to/news.db>", file=sys.stderr)
    sys.exit(1)
  migrate(Path(sys.argv[1]))
