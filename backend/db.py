"""PostgreSQL connection pool and schema initialization."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator, LiteralString, Sequence, cast

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
  health                TEXT NOT NULL DEFAULT 'ok'
                        CONSTRAINT feeds_health_state
                        CHECK (health IN ('ok', 'degraded', 'failing')),
  extraction_rules      TEXT,
  fetch_interval_min    INTEGER NOT NULL DEFAULT 10
                        CONSTRAINT feeds_fetch_interval_bucket
                        CHECK (fetch_interval_min IN (10, 15, 30, 60, 120, 240, 360)),
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
CREATE INDEX IF NOT EXISTS idx_articles_fetched_at ON articles(fetched_at);
-- Matches list_articles' default date ordering, so the planner can read it
-- in order for a LIMIT and use it for `since_days` range scans.
CREATE INDEX IF NOT EXISTS idx_articles_date_order ON articles(
  (COALESCE(published, fetched_at)) DESC NULLS LAST, fetched_at DESC, url DESC
);
CREATE INDEX IF NOT EXISTS idx_feeds_folder_id    ON feeds(folder_id);
"""

_SCHEMA_VERSION_TABLE = """\
CREATE TABLE IF NOT EXISTS schema_version (
  id      BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
  version INTEGER NOT NULL
);
"""

# Versioned migrations: entry N (1-based) brings a database to version N.
# Append new entries; never edit or reorder existing ones, since databases
# record how many they have applied. Each entry runs inside the migration
# transaction, so it must be transaction-safe (no CREATE INDEX CONCURRENTLY).
_MIGRATIONS: list[LiteralString] = [
  # 1: columns / constraints added after the first release. Idempotent, as it
  # also runs on databases that already had them before versioning existed.
  """\
ALTER TABLE feeds
  ADD COLUMN IF NOT EXISTS fetch_interval_min INTEGER NOT NULL DEFAULT 10;
ALTER TABLE feeds
  ADD COLUMN IF NOT EXISTS next_fetch_at TIMESTAMPTZ;
ALTER TABLE feeds DROP CONSTRAINT IF EXISTS feeds_fetch_interval_bucket;
ALTER TABLE feeds ADD CONSTRAINT feeds_fetch_interval_bucket
  CHECK (fetch_interval_min IN (10, 15, 30, 60, 120, 240, 360));
ALTER TABLE feeds
  ADD COLUMN IF NOT EXISTS health TEXT NOT NULL DEFAULT 'ok';
ALTER TABLE feeds DROP CONSTRAINT IF EXISTS feeds_health_state;
ALTER TABLE feeds ADD CONSTRAINT feeds_health_state
  CHECK (health IN ('ok', 'degraded', 'failing'));
CREATE INDEX IF NOT EXISTS idx_feeds_next_fetch
  ON feeds(next_fetch_at) WHERE enabled;
DELETE FROM filters f USING filters g
  WHERE f.pattern = g.pattern AND f.target = g.target AND f.id > g.id;
CREATE UNIQUE INDEX IF NOT EXISTS filters_pattern_target_uniq
  ON filters(pattern, target);
""",
  # 2: fresh installs got the fetch_interval_min CHECK twice (unnamed in
  # _SCHEMA, plus feeds_fetch_interval_bucket from migration 1).
  "ALTER TABLE feeds DROP CONSTRAINT IF EXISTS feeds_fetch_interval_min_check",
]

# Arbitrary but stable advisory lock key serializing `init_schema` runs.
_SCHEMA_LOCK_KEY = 4_919_432

_DEFAULT_URL = "postgresql://stingray:stingray@postgres:5432/stingray"
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
  """Bring the database schema up to date (see migrate)."""
  with connection() as conn:
    migrate(conn)


def _read_version(conn: Conn) -> int:
  """Applied migration count; 0 without a schema_version table or row."""
  row = conn.execute("SELECT to_regclass('schema_version') AS oid").fetchone()
  if row is None or row["oid"] is None:
    return 0
  row = conn.execute("SELECT version FROM schema_version").fetchone()
  return row["version"] if row else 0


def _check_not_newer(current: int, latest: int) -> None:
  if current > latest:
    raise RuntimeError(
      f"Database schema is at version {current}, but this code supports {latest}; "
      "upgrade the application"
    )


def migrate(conn: Conn, migrations: Sequence[LiteralString] = _MIGRATIONS) -> None:
  """Apply pending migrations in the caller's transaction.

  web and every fetcher run call this, so an up-to-date database costs two
  reads and no advisory lock or DDL. Otherwise an advisory lock serializes concurrent
  runs, which would deadlock upgrading their locks on `feeds`; the version is
  re-read under the lock (READ COMMITTED) in case another run just finished.
  Version 0 means a fresh or pre-versioning database: _SCHEMA is idempotent,
  so it is created or completed before migration 1.
  """
  latest = len(migrations)
  current = _read_version(conn)
  _check_not_newer(current, latest)
  if current == latest:
    return
  conn.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_LOCK_KEY,))
  current = _read_version(conn)
  _check_not_newer(current, latest)
  if current == latest:
    return
  if current == 0:
    conn.execute(_SCHEMA)
    conn.execute(_SCHEMA_VERSION_TABLE)
  for sql in migrations[current:]:
    conn.execute(sql)
  conn.execute(
    "INSERT INTO schema_version (version) VALUES (%s) "
    "ON CONFLICT (id) DO UPDATE SET version = EXCLUDED.version",
    (latest,),
  )
