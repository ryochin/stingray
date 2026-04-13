"""SQLite connection management and schema initialization."""

from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS feeds (
  id         INTEGER PRIMARY KEY,
  name       TEXT NOT NULL,
  type       TEXT NOT NULL CHECK (type IN ('rss','reddit')),
  url        TEXT,
  subreddit  TEXT,
  sort       TEXT,
  lang       TEXT NOT NULL DEFAULT 'en',
  max_items  INTEGER NOT NULL DEFAULT 20,
  summarize  INTEGER NOT NULL DEFAULT 1,
  enabled    INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS articles (
  url             TEXT PRIMARY KEY,
  feed_id         INTEGER REFERENCES feeds(id) ON DELETE SET NULL,
  title           TEXT NOT NULL,
  title_ja        TEXT,
  source          TEXT NOT NULL,
  published       TEXT,
  content_snippet TEXT,
  summary         TEXT,
  lang            TEXT,
  fetched_at      TEXT NOT NULL
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

CREATE INDEX IF NOT EXISTS idx_articles_feed      ON articles(feed_id);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published DESC);
"""

_db_path: Path = Path("data/news.db")


def configure(path: str | Path) -> None:
  """Set the database file path. Must be called before get_conn()."""
  global _db_path
  _db_path = Path(path)


def get_conn() -> sqlite3.Connection:
  """Open a connection with WAL mode and recommended PRAGMAs."""
  _db_path.parent.mkdir(parents=True, exist_ok=True)
  conn = sqlite3.connect(str(_db_path), timeout=10)
  conn.row_factory = sqlite3.Row
  conn.execute("PRAGMA journal_mode = WAL")
  conn.execute("PRAGMA synchronous = NORMAL")
  conn.execute("PRAGMA foreign_keys = ON")
  conn.execute("PRAGMA busy_timeout = 5000")
  return conn


def init_schema() -> None:
  """Create tables and indexes if they don't exist."""
  conn = get_conn()
  try:
    conn.executescript(_SCHEMA)
    conn.commit()
  finally:
    conn.close()
