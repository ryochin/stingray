"""Tests for healthcheck.py job-age logic.

Covers the three-way branch in _check_recent_job:
  - no job record → startup grace (OK)
  - job running but within 2× max_age → OK
  - job running and exceeded 2× max_age → fail
  - finished job within max_age → OK
  - finished job older than max_age → fail
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import db
import healthcheck


def _insert_job(
  *,
  started_at: datetime,
  finished_at: datetime | None,
  status: str = "running",
) -> None:
  with db.connection() as conn:
    conn.execute(
      """INSERT INTO refresh_jobs (started_at, finished_at, source, status)
         VALUES (%s, %s, 'test', %s)""",
      (started_at, finished_at, status),
    )


pytestmark = pytest.mark.usefixtures("clean_db")


class TestCheckRecentJob:
  def test_no_job_is_healthy_startup_grace(self):
    # No refresh_jobs row → container is considered healthy (fresh boot).
    healthcheck._check_recent_job(max_age_minutes=45)

  def test_running_job_within_grace_is_healthy(self):
    now = datetime.now(tz=timezone.utc)
    _insert_job(started_at=now - timedelta(minutes=10), finished_at=None)
    healthcheck._check_recent_job(max_age_minutes=45)

  def test_long_running_job_fails(self):
    # Job running for > 2× max_age_minutes → considered stuck.
    now = datetime.now(tz=timezone.utc)
    _insert_job(started_at=now - timedelta(minutes=200), finished_at=None)
    with pytest.raises(RuntimeError, match="has been running"):
      healthcheck._check_recent_job(max_age_minutes=45)

  def test_finished_recent_is_healthy(self):
    now = datetime.now(tz=timezone.utc)
    _insert_job(
      started_at=now - timedelta(minutes=20),
      finished_at=now - timedelta(minutes=5),
      status="success",
    )
    healthcheck._check_recent_job(max_age_minutes=45)

  def test_finished_stale_fails(self):
    now = datetime.now(tz=timezone.utc)
    _insert_job(
      started_at=now - timedelta(hours=3),
      finished_at=now - timedelta(hours=2),
      status="success",
    )
    with pytest.raises(RuntimeError, match="Last job finished"):
      healthcheck._check_recent_job(max_age_minutes=45)
