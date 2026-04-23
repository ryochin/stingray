"""PostgreSQL connection pool and schema initialization."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator, cast

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

Row = dict[str, Any]
Conn = psycopg.Connection[Row]

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS folders (
  id         INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  name       TEXT NOT NULL,
  position   INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS feeds (
  id                    INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  name                  TEXT NOT NULL,
  url                   TEXT,
  site_url              TEXT,
  translate             BOOLEAN NOT NULL DEFAULT FALSE,
  summarize             BOOLEAN NOT NULL DEFAULT TRUE,
  enabled               BOOLEAN NOT NULL DEFAULT TRUE,
  folder_id             INTEGER REFERENCES folders(id) ON DELETE SET NULL,
  position              INTEGER NOT NULL DEFAULT 0,
  last_fetched_at       TIMESTAMPTZ,
  consecutive_failures  INTEGER NOT NULL DEFAULT 0,
  last_error            TEXT,
  extraction_rules      TEXT,
  fetch_interval_min    INTEGER NOT NULL DEFAULT 15
                        CHECK (fetch_interval_min IN (15, 30, 60, 120, 240, 360)),
  next_fetch_at         TIMESTAMPTZ,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS articles (
  url                TEXT PRIMARY KEY,
  feed_id            INTEGER REFERENCES feeds(id) ON DELETE CASCADE,
  title              TEXT NOT NULL,
  title_translated   TEXT,
  source             TEXT NOT NULL,
  published          TIMESTAMPTZ,
  content_snippet    TEXT,
  summary            TEXT,
  content_html       TEXT,
  content_translated TEXT,
  fetched_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  read_at            TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS refresh_jobs (
  id          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  started_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finished_at TIMESTAMPTZ,
  source      TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'running',
  new_count   INTEGER,
  error       TEXT
);

CREATE TABLE IF NOT EXISTS filters (
  id         INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pattern    TEXT NOT NULL,
  target     TEXT NOT NULL DEFAULT 'title',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_articles_feed      ON articles(feed_id);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published DESC);
CREATE INDEX IF NOT EXISTS idx_articles_read_at   ON articles(read_at);
CREATE INDEX IF NOT EXISTS idx_feeds_folder_id    ON feeds(folder_id);
"""

# Idempotent migrations for existing databases that pre-date newer columns /
# constraints. Each statement is safe to run on an already-migrated schema.
_MIGRATIONS = """\
ALTER TABLE feeds
  ADD COLUMN IF NOT EXISTS fetch_interval_min INTEGER NOT NULL DEFAULT 15;
ALTER TABLE feeds
  ADD COLUMN IF NOT EXISTS next_fetch_at TIMESTAMPTZ;
ALTER TABLE feeds DROP CONSTRAINT IF EXISTS feeds_fetch_interval_bucket;
ALTER TABLE feeds ADD CONSTRAINT feeds_fetch_interval_bucket
  CHECK (fetch_interval_min IN (15, 30, 60, 120, 240, 360));
CREATE INDEX IF NOT EXISTS idx_feeds_next_fetch
  ON feeds(next_fetch_at) WHERE enabled;
"""

_DEFAULT_URL = "postgresql://news:news@localhost:25432/news"
_pool: ConnectionPool | None = None


def configure(database_url: str | None = None) -> None:
  """Initialize the connection pool. Must be called before connection()."""
  global _pool
  if _pool is not None:
    _pool.close()
  url = database_url or os.environ.get("DATABASE_URL", _DEFAULT_URL)
  _pool = ConnectionPool(
    url,
    min_size=1,
    max_size=10,
    kwargs={"row_factory": dict_row},
    open=True,
  )
  _pool.wait()


def close() -> None:
  """Close the connection pool. Safe to call if not configured."""
  global _pool
  if _pool is not None:
    _pool.close()
    _pool = None


@contextmanager
def connection() -> Iterator[Conn]:
  """Acquire a pooled connection (context manager). Auto-commits on exit."""
  if _pool is None:
    raise RuntimeError("db.configure() must be called first")
  with _pool.connection() as conn:
    yield cast(Conn, conn)


def init_schema() -> None:
  """Create tables and indexes if they don't exist, and apply idempotent
  migrations for databases that pre-date newer columns / constraints.
  """
  with connection() as conn:
    conn.execute(_SCHEMA)
    conn.execute(_MIGRATIONS)
