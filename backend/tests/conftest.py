"""Pytest fixtures.

DB fixtures give Rails-like isolation:
  - session fixture `db_session` provisions a dedicated test database,
    configures the connection pool, and loads the schema once.
  - function fixture `clean_db` truncates all tables between tests.
  - Tests opt in by requesting `clean_db` (which depends on `db_session`);
    pure unit tests (lang, opml) skip DB plumbing entirely.
  - If Postgres is unreachable, DB-requiring tests skip rather than fail.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse, urlunparse

import psycopg
import pytest

import db


def _test_db_url() -> str:
  return os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://news:news@localhost:25432/news_test",
  )


def _admin_url(test_url: str) -> str:
  # Connect to the default 'postgres' DB to CREATE the test DB if missing.
  parsed = urlparse(test_url)
  return urlunparse(parsed._replace(path="/postgres"))


def _ensure_test_db(test_url: str) -> None:
  test_db = urlparse(test_url).path.lstrip("/")
  with psycopg.connect(_admin_url(test_url), autocommit=True) as conn:
    exists = conn.execute(
      "SELECT 1 FROM pg_database WHERE datname = %s", (test_db,)
    ).fetchone()
    if not exists:
      # Identifier quoting: test_db is derived from env/config, not user input.
      conn.execute(f'CREATE DATABASE "{test_db}"')


@pytest.fixture(scope="session")
def db_session():
  test_url = _test_db_url()
  try:
    _ensure_test_db(test_url)
  except psycopg.OperationalError as e:
    pytest.skip(f"Postgres unreachable ({test_url}): {e}")
  db.configure(test_url)
  db.init_schema()
  yield
  db.close()


@pytest.fixture
def clean_db(db_session):
  """Truncate all app tables before each test. RESTART IDENTITY resets sequences."""
  with db.connection() as conn:
    conn.execute(
      "TRUNCATE TABLE articles, feeds, folders, refresh_jobs, filters "
      "RESTART IDENTITY CASCADE"
    )
  yield
