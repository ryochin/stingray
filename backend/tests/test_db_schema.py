"""Versioned schema migrations (db.migrate).

Each test gets a throwaway database so it can start from an empty, legacy, or
partially migrated state without touching the shared test database.
"""

from __future__ import annotations

import os
import uuid
from typing import Any, Iterator
from urllib.parse import urlparse, urlunparse

import psycopg
import pytest
from psycopg.rows import dict_row

import db
from tests.conftest import _admin_url, _ensure_test_db, _test_db_url

PROBES = [
  "CREATE TABLE probe (n INTEGER NOT NULL)",
  "INSERT INTO probe VALUES (2)",
  "INSERT INTO probe VALUES (3)",
]


@pytest.fixture
def conn() -> Iterator[db.Conn]:
  test_url = _test_db_url()
  try:
    _ensure_test_db(test_url)
  except psycopg.OperationalError as e:
    if os.environ.get("REQUIRE_DB"):
      raise
    pytest.skip(f"Postgres unreachable ({test_url}): {e}")
  name = f"{urlparse(test_url).path.lstrip('/')}_schema_{uuid.uuid4().hex[:8]}"
  admin = _admin_url(test_url)
  with psycopg.connect(admin, autocommit=True) as admin_conn:
    admin_conn.execute(f'CREATE DATABASE "{name}"')
  try:
    url = urlunparse(urlparse(test_url)._replace(path=f"/{name}"))
    with psycopg.connect(url, row_factory=dict_row) as c:
      yield c
  finally:
    with psycopg.connect(admin, autocommit=True) as admin_conn:
      admin_conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


class RecordingConn:
  """Delegates to a connection and records every SQL string it executes."""

  def __init__(self, inner: db.Conn) -> None:
    self.inner = inner
    self.sql: list[str] = []

  def execute(self, query: Any, params: Any = None) -> Any:
    self.sql.append(str(query))
    return self.inner.execute(query, params)


def _version(conn: db.Conn) -> int | None:
  if _exists(conn, "schema_version"):
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    return row["version"] if row else None
  return None


def _exists(conn: db.Conn, table: str) -> bool:
  row = conn.execute("SELECT to_regclass(%s) AS oid", (table,)).fetchone()
  return row is not None and row["oid"] is not None


def _feeds_checks(conn: db.Conn) -> list[str]:
  rows = conn.execute(
    "SELECT conname FROM pg_constraint "
    "WHERE conrelid = 'feeds'::regclass AND contype = 'c' ORDER BY conname"
  ).fetchall()
  return [r["conname"] for r in rows]


def _probe_rows(conn: db.Conn) -> list[int]:
  return [r["n"] for r in conn.execute("SELECT n FROM probe ORDER BY n").fetchall()]


def _make_legacy(conn: db.Conn) -> None:
  """A database created by the pre-versioning code: no schema_version table,
  and the duplicate unnamed fetch_interval_min CHECK a fresh install got."""
  conn.execute(db._SCHEMA)
  conn.execute(
    "ALTER TABLE feeds ADD CONSTRAINT feeds_fetch_interval_min_check "
    "CHECK (fetch_interval_min IN (10, 15, 30, 60, 120, 240, 360))"
  )
  conn.execute(db._MIGRATIONS[0])
  conn.commit()


EXPECTED_CHECKS = ["feeds_fetch_interval_bucket", "feeds_health_state"]


# --- starting states ---

def test_empty_database_is_created_at_latest_version(conn: db.Conn) -> None:
  db.migrate(conn)
  conn.commit()
  assert _version(conn) == len(db._MIGRATIONS)
  for table in ("folders", "feeds", "articles", "refresh_jobs", "filters"):
    assert _exists(conn, table)
  assert _feeds_checks(conn) == EXPECTED_CHECKS


def test_legacy_database_is_upgraded_and_loses_the_duplicate_check(conn: db.Conn) -> None:
  _make_legacy(conn)
  assert "feeds_fetch_interval_min_check" in _feeds_checks(conn)
  conn.execute("INSERT INTO feeds (name, url) VALUES ('kept', 'https://example.com/feed')")
  conn.commit()

  db.migrate(conn)
  conn.commit()

  assert _version(conn) == len(db._MIGRATIONS)
  assert _feeds_checks(conn) == EXPECTED_CHECKS
  assert conn.execute("SELECT name FROM feeds").fetchall() == [{"name": "kept"}]


def test_version_table_without_a_row_counts_as_version_zero(conn: db.Conn) -> None:
  db.migrate(conn, PROBES[:1])
  conn.execute("DELETE FROM schema_version")
  conn.execute("DROP TABLE probe")
  conn.commit()

  db.migrate(conn, PROBES)
  conn.commit()

  assert _version(conn) == 3
  assert _probe_rows(conn) == [2, 3]


# --- incremental and idempotent runs ---

def test_only_pending_migrations_run(conn: db.Conn) -> None:
  db.migrate(conn, PROBES[:2])
  conn.commit()
  assert _probe_rows(conn) == [2]

  db.migrate(conn, PROBES)
  conn.commit()

  assert _version(conn) == 3
  assert _probe_rows(conn) == [2, 3]


def test_up_to_date_database_runs_no_lock_and_no_DDL(conn: db.Conn) -> None:
  db.migrate(conn, PROBES)
  conn.commit()

  recording = RecordingConn(conn)
  db.migrate(recording, PROBES)  # type: ignore[arg-type]

  assert len(recording.sql) == 2
  assert not any("advisory" in sql or "CREATE" in sql or "ALTER" in sql for sql in recording.sql)
  assert _probe_rows(conn) == [2, 3]


def test_version_is_reread_after_taking_the_lock(
  conn: db.Conn, monkeypatch: pytest.MonkeyPatch,
) -> None:
  # Another process migrated between our unlocked read and the lock: the
  # stale first read must not make us re-run migrations.
  db.migrate(conn, PROBES)
  conn.commit()

  real_read = db._read_version
  recording = RecordingConn(conn)
  reads: list[int] = []

  def stale_then_real(c: db.Conn) -> int:
    reads.append(1)
    recording.sql.append("<read>")
    return 1 if len(reads) == 1 else real_read(c)

  monkeypatch.setattr(db, "_read_version", stale_then_real)
  db.migrate(recording, PROBES)  # type: ignore[arg-type]
  conn.commit()

  events = [e if e == "<read>" else ("<lock>" if "advisory" in e else e) for e in recording.sql]
  assert events[:3] == ["<read>", "<lock>", "<read>"]
  assert _probe_rows(conn) == [2, 3]



# --- migration 3: unique feed URLs ---

def _at_version_2(conn: db.Conn, *feeds: tuple[str, str | None]) -> None:
  db.migrate(conn, db._MIGRATIONS[:2])
  for name, url in feeds:
    conn.execute("INSERT INTO feeds (name, url) VALUES (%s, %s)", (name, url))
  conn.commit()


def test_feed_url_becomes_unique_and_null_urls_stay_allowed(conn: db.Conn) -> None:
  _at_version_2(conn, ("a", "https://a.example.com/"), ("n1", None), ("n2", None))

  db.migrate(conn)
  conn.commit()

  assert _version(conn) == 3
  conn.execute("INSERT INTO feeds (name, url) VALUES ('n3', NULL)")
  with pytest.raises(psycopg.errors.UniqueViolation) as excinfo:
    conn.execute("INSERT INTO feeds (name, url) VALUES ('a2', 'https://a.example.com/')")
  assert excinfo.value.diag.constraint_name == db.FEEDS_URL_UNIQUE_INDEX
  conn.rollback()


def test_duplicate_feed_urls_stop_the_migration_and_name_the_url(conn: db.Conn) -> None:
  _at_version_2(
    conn,
    ("a1", "https://dup.example.com/"),
    ("a2", "https://dup.example.com/"),
    ("b", "https://b.example.com/"),
  )

  with pytest.raises(psycopg.errors.RaiseException, match="https://dup.example.com/"):
    db.migrate(conn)
  conn.rollback()

  assert _version(conn) == 2
  assert not _exists(conn, db.FEEDS_URL_UNIQUE_INDEX)


# --- failures ---

def test_failed_migration_rolls_everything_back(conn: db.Conn) -> None:
  db.migrate(conn, PROBES[:2])
  conn.commit()

  with pytest.raises(psycopg.errors.DivisionByZero):
    db.migrate(conn, [*PROBES, "CREATE TABLE probe_later (n INTEGER)", "SELECT 1 / 0"])
  conn.rollback()

  assert _version(conn) == 2
  assert _probe_rows(conn) == [2]
  assert not _exists(conn, "probe_later")


def test_failed_first_migration_leaves_an_empty_database(conn: db.Conn) -> None:
  with pytest.raises(psycopg.errors.DivisionByZero):
    db.migrate(conn, ["SELECT 1 / 0"])
  conn.rollback()

  assert not _exists(conn, "schema_version")
  assert not _exists(conn, "feeds")


def test_database_newer_than_code_refuses_to_start(conn: db.Conn) -> None:
  db.migrate(conn, PROBES)
  conn.commit()

  recording = RecordingConn(conn)
  with pytest.raises(RuntimeError, match=r"version 3.*supports 2"):
    db.migrate(recording, PROBES[:2])  # type: ignore[arg-type]

  assert not any("INSERT" in sql or "advisory" in sql for sql in recording.sql)
  assert _version(conn) == 3
