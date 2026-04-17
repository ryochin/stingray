"""SQLite connection management and schema initialization."""

from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS folders (
  id         INTEGER PRIMARY KEY,
  name       TEXT NOT NULL,
  position   INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feeds (
  id                    INTEGER PRIMARY KEY,
  name                  TEXT NOT NULL,
  url                   TEXT,
  site_url              TEXT,
  lang                  TEXT NOT NULL DEFAULT 'en',
  translate             INTEGER NOT NULL DEFAULT 0,
  max_items             INTEGER NOT NULL DEFAULT 20,
  summarize             INTEGER NOT NULL DEFAULT 1,
  enabled               INTEGER NOT NULL DEFAULT 1,
  folder_id             INTEGER REFERENCES folders(id) ON DELETE SET NULL,
  last_fetched_at       TEXT,
  consecutive_failures  INTEGER NOT NULL DEFAULT 0,
  last_error            TEXT,
  created_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS articles (
  url             TEXT PRIMARY KEY,
  feed_id         INTEGER REFERENCES feeds(id) ON DELETE CASCADE,
  title           TEXT NOT NULL,
  title_ja        TEXT,
  title_translated TEXT,
  source          TEXT NOT NULL,
  published       TEXT,
  content_snippet TEXT,
  summary         TEXT,
  content_html    TEXT,
  content_translated TEXT,
  lang            TEXT,
  fetched_at      TEXT NOT NULL,
  read_at         TEXT
);

CREATE TABLE IF NOT EXISTS refresh_jobs (
  id          INTEGER PRIMARY KEY,
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  source      TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'running',
  new_count   INTEGER,
  error       TEXT
);

CREATE TABLE IF NOT EXISTS filters (
  id         INTEGER PRIMARY KEY,
  pattern    TEXT NOT NULL,
  target     TEXT NOT NULL DEFAULT 'title',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_articles_feed      ON articles(feed_id);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published DESC);
CREATE INDEX IF NOT EXISTS idx_articles_read_at   ON articles(read_at);
CREATE INDEX IF NOT EXISTS idx_feeds_folder_id    ON feeds(folder_id);
"""

_MIGRATIONS = [
  # Migration 1: add read_at column
  """\
  ALTER TABLE articles ADD COLUMN read_at TEXT;
  """,
  # Migration 2: folders table
  """\
  CREATE TABLE IF NOT EXISTS folders (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    position   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
  );
  """,
  # Migration 3: add folder_id to feeds
  """\
  ALTER TABLE feeds ADD COLUMN folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL;
  """,
  # Migration 4: filters table (originally ng_words)
  """\
  CREATE TABLE IF NOT EXISTS filters (
    id         INTEGER PRIMARY KEY,
    pattern    TEXT NOT NULL,
    target     TEXT NOT NULL DEFAULT 'title',
    created_at TEXT NOT NULL
  );
  """,
  # Migration 5: add content_html to articles
  """\
  ALTER TABLE articles ADD COLUMN content_html TEXT;
  """,
  # Migration 6: add site_url to feeds
  """\
  ALTER TABLE feeds ADD COLUMN site_url TEXT;
  """,
]

_db_path: Path = Path("data/news.db")


def configure(path: str | Path) -> None:
  """Set the database file path. Must be called before get_conn()."""
  global _db_path
  _db_path = Path(path)


def get_conn() -> sqlite3.Connection:
  """Open a connection with WAL mode and recommended PRAGMAs."""
  _db_path.parent.mkdir(parents=True, exist_ok=True)
  conn = sqlite3.connect(str(_db_path), timeout=5)
  conn.row_factory = sqlite3.Row
  conn.execute("PRAGMA journal_mode = WAL")
  conn.execute("PRAGMA synchronous = NORMAL")
  conn.execute("PRAGMA foreign_keys = ON")
  conn.execute("PRAGMA busy_timeout = 5000")
  return conn


def _run_migrations(conn: sqlite3.Connection) -> None:
  """Apply pending schema migrations."""
  cols = {r[1] for r in conn.execute("PRAGMA table_info(articles)").fetchall()}
  if "read_at" not in cols:
    conn.execute(_MIGRATIONS[0])
    conn.commit()

  tables = {r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table'"
  ).fetchall()}
  if "folders" not in tables:
    for sql in _MIGRATIONS[1:3]:
      conn.execute(sql)
    conn.commit()
    tables.add("folders")

  if "filters" not in tables and "ng_words" not in tables:
    conn.execute(_MIGRATIONS[3])
    conn.commit()
  elif "ng_words" in tables and "filters" not in tables:
    conn.execute("ALTER TABLE ng_words RENAME TO filters")
    conn.commit()

  art_cols = {r[1] for r in conn.execute("PRAGMA table_info(articles)").fetchall()}
  if "content_html" not in art_cols:
    conn.execute(_MIGRATIONS[4])
    conn.commit()

  feed_cols = {r[1] for r in conn.execute("PRAGMA table_info(feeds)").fetchall()}
  if "site_url" not in feed_cols:
    conn.execute(_MIGRATIONS[5])
    conn.commit()
    feed_cols.add("site_url")

  if "last_fetched_at" not in feed_cols:
    conn.execute("ALTER TABLE feeds ADD COLUMN last_fetched_at TEXT")
    conn.execute("ALTER TABLE feeds ADD COLUMN consecutive_failures INTEGER NOT NULL DEFAULT 0")
    conn.execute("ALTER TABLE feeds ADD COLUMN last_error TEXT")
    conn.commit()
    feed_cols.update(("last_fetched_at", "consecutive_failures", "last_error"))

  if "translate" not in feed_cols:
    conn.execute("ALTER TABLE feeds ADD COLUMN translate INTEGER NOT NULL DEFAULT 0")
    conn.execute("UPDATE feeds SET translate = 1 WHERE lang != 'ja'")
    conn.commit()

  art_cols = {r[1] for r in conn.execute("PRAGMA table_info(articles)").fetchall()}
  if "title_translated" not in art_cols:
    conn.execute("ALTER TABLE articles ADD COLUMN title_translated TEXT")
    conn.execute("ALTER TABLE articles ADD COLUMN content_translated TEXT")
    conn.execute("UPDATE articles SET title_translated = title_ja WHERE title_ja IS NOT NULL")
    conn.commit()

  # Migrate articles foreign key from SET NULL to CASCADE
  fk_info = conn.execute("PRAGMA foreign_key_list(articles)").fetchall()
  needs_cascade = True
  for fk in fk_info:
    if fk[2] == "feeds" and fk[6] == "CASCADE":
      needs_cascade = False
      break
  if needs_cascade and fk_info:
    conn.execute("PRAGMA foreign_keys = OFF")
    art_cols = [r[1] for r in conn.execute("PRAGMA table_info(articles)").fetchall()]
    cols = ", ".join(art_cols)
    conn.execute(f"""\
      CREATE TABLE articles_new (
        url             TEXT PRIMARY KEY,
        feed_id         INTEGER REFERENCES feeds(id) ON DELETE CASCADE,
        title           TEXT NOT NULL,
        title_ja        TEXT,
        title_translated TEXT,
        source          TEXT NOT NULL,
        published       TEXT,
        content_snippet TEXT,
        summary         TEXT,
        content_html    TEXT,
        content_translated TEXT,
        lang            TEXT,
        fetched_at      TEXT NOT NULL,
        read_at         TEXT
      )""")
    conn.execute(f"INSERT INTO articles_new ({cols}) SELECT {cols} FROM articles")
    conn.execute("DROP TABLE articles")
    conn.execute("ALTER TABLE articles_new RENAME TO articles")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_feed ON articles(feed_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_read_at ON articles(read_at)")
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")


def init_schema() -> None:
  """Create tables and indexes if they don't exist."""
  conn = get_conn()
  try:
    conn.executescript(_SCHEMA)
    _run_migrations(conn)
  finally:
    conn.close()
