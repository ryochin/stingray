"""Integration tests for adaptive fetch-interval scheduling.

Covers repo.record_feed_attempt, repo.list_due_feeds, repo.toggle_feed's
enabled-transition reset, repo.advisory_lock, and the schedule_next_at /
step_bucket helpers. Also exercises the fetch_interval_min CHECK constraint
and the outcome classifier in fetcher._classify_outcome.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import psycopg
import pytest

import db
import repo
from fetcher import _classify_outcome


def _make_feed(
  *,
  name: str = "F",
  enabled: bool = True,
  fetch_interval_min: int = 10,
  next_fetch_at: datetime | None = None,
  consecutive_failures: int = 0,
  last_error: str | None = None,
) -> int:
  with db.connection() as conn:
    row = conn.execute(
      """INSERT INTO feeds
           (name, url, enabled, fetch_interval_min, next_fetch_at,
            consecutive_failures, last_error)
         VALUES (%s, %s, %s, %s, %s, %s, %s)
         RETURNING id""",
      (
        name, f"https://{name}.example.com/rss",
        enabled, fetch_interval_min, next_fetch_at,
        consecutive_failures, last_error,
      ),
    ).fetchone()
    assert row is not None
    return int(row["id"])


def _read_feed(feed_id: int) -> dict:
  with db.connection() as conn:
    row = conn.execute(
      "SELECT fetch_interval_min, next_fetch_at, consecutive_failures, "
      "last_error, last_fetched_at, enabled "
      "FROM feeds WHERE id = %s",
      (feed_id,),
    ).fetchone()
    assert row is not None
    return dict(row)


# -- step_bucket --


class TestStepBucket:
  def test_shrink_from_middle(self):
    assert repo.step_bucket(60, direction=-1) == 30

  def test_shrink_clamps_at_min(self):
    assert repo.step_bucket(10, direction=-1) == 10

  def test_grow_from_middle(self):
    assert repo.step_bucket(60, direction=+1) == 120

  def test_grow_clamps_at_max(self):
    assert repo.step_bucket(360, direction=+1) == 360

  def test_non_bucket_input_snaps_to_nearest(self):
    # Shouldn't happen in practice thanks to CHECK constraint, but the helper
    # must remain well-defined if called directly.
    assert repo.step_bucket(45, direction=+1) in repo.BUCKETS


# -- schedule_next_at --


class TestScheduleNextAt:
  def test_lands_on_tick_boundary(self):
    now = datetime(2026, 4, 22, 10, 3, 0, tzinfo=timezone.utc)
    for _ in range(20):
      next_at = repo.schedule_next_at(now, 10)
      # Seconds since epoch must be a multiple of 10 minutes.
      assert int(next_at.timestamp()) % (10 * 60) == 0

  def test_always_in_the_future(self):
    now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
    for _ in range(20):
      assert repo.schedule_next_at(now, 10) > now

  def test_roughly_matches_interval(self):
    now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
    for _ in range(20):
      delta = repo.schedule_next_at(now, 60) - now
      # 60 min ± 10% jitter, then ceil to next 10-min tick → range [60, 70].
      assert timedelta(minutes=59) <= delta <= timedelta(minutes=71)


# -- record_feed_attempt --


class TestRecordFeedAttempt:
  def test_fresh_shrinks_bucket(self, clean_db):
    fid = _make_feed(fetch_interval_min=60, consecutive_failures=2,
                     last_error="prev")
    repo.record_feed_attempt(fid, "fresh")
    row = _read_feed(fid)
    assert row["fetch_interval_min"] == 30
    assert row["consecutive_failures"] == 0
    assert row["last_error"] is None
    assert row["last_fetched_at"] is not None
    assert row["next_fetch_at"] is not None

  def test_fresh_clamps_at_minimum(self, clean_db):
    fid = _make_feed(fetch_interval_min=10)
    repo.record_feed_attempt(fid, "fresh")
    assert _read_feed(fid)["fetch_interval_min"] == 10

  def test_miss_grows_bucket(self, clean_db):
    fid = _make_feed(fetch_interval_min=60)
    repo.record_feed_attempt(fid, "miss")
    assert _read_feed(fid)["fetch_interval_min"] == 120

  def test_miss_clamps_at_maximum(self, clean_db):
    fid = _make_feed(fetch_interval_min=360)
    repo.record_feed_attempt(fid, "miss")
    assert _read_feed(fid)["fetch_interval_min"] == 360

  def test_failure_records_error_and_increments_counter(self, clean_db):
    fid = _make_feed(fetch_interval_min=60)
    repo.record_feed_attempt(fid, "failure", error="boom")
    row = _read_feed(fid)
    assert row["fetch_interval_min"] == 60  # bucket unchanged
    assert row["consecutive_failures"] == 1
    assert row["last_error"] == "boom"

  def test_failure_never_moves_schedule_earlier(self, clean_db):
    far_future = datetime.now(tz=timezone.utc) + timedelta(hours=12)
    fid = _make_feed(
      fetch_interval_min=360, next_fetch_at=far_future, consecutive_failures=0,
    )
    repo.record_feed_attempt(fid, "failure", error="err")
    row = _read_feed(fid)
    # A 360-minute bucket with a ~6h distant next_fetch_at must not be pulled
    # earlier by a first-failure backoff of 15 minutes.
    assert row["next_fetch_at"] >= far_future

  def test_recovery_clears_failures_and_error(self, clean_db):
    fid = _make_feed(
      fetch_interval_min=60, consecutive_failures=3, last_error="prev",
    )
    repo.record_feed_attempt(fid, "fresh")
    row = _read_feed(fid)
    assert row["consecutive_failures"] == 0
    assert row["last_error"] is None
    assert row["fetch_interval_min"] == 30

  def test_degraded_keeps_bucket_and_state(self, clean_db):
    fid = _make_feed(
      fetch_interval_min=120, consecutive_failures=2, last_error="prev",
    )
    repo.record_feed_attempt(fid, "degraded")
    row = _read_feed(fid)
    assert row["fetch_interval_min"] == 120
    assert row["consecutive_failures"] == 2
    assert row["last_error"] == "prev"  # not wiped
    assert row["last_fetched_at"] is not None
    assert row["next_fetch_at"] is not None

  def test_degraded_with_error_overwrites_last_error(self, clean_db):
    fid = _make_feed(fetch_interval_min=60, last_error=None)
    repo.record_feed_attempt(
      fid, "degraded", error="extraction rules not configured",
    )
    assert _read_feed(fid)["last_error"] == "extraction rules not configured"

  def test_degraded_to_fresh_clears_last_error(self, clean_db):
    fid = _make_feed(fetch_interval_min=60, last_error="web-norules")
    repo.record_feed_attempt(fid, "fresh")
    assert _read_feed(fid)["last_error"] is None

  def test_missing_feed_is_noop(self, clean_db):
    # Should silently ignore IDs that don't exist.
    repo.record_feed_attempt(999999, "fresh")


# -- list_due_feeds --


class TestListDueFeeds:
  def test_force_false_selects_null_and_past(self, clean_db):
    past = datetime.now(tz=timezone.utc) - timedelta(minutes=5)
    future = datetime.now(tz=timezone.utc) + timedelta(hours=1)
    null_id = _make_feed(name="A", next_fetch_at=None)
    past_id = _make_feed(name="B", next_fetch_at=past)
    _make_feed(name="C", next_fetch_at=future)
    due_ids = {f.id for f in repo.list_due_feeds(force=False)}
    assert due_ids == {null_id, past_id}

  def test_force_true_returns_all_enabled(self, clean_db):
    future = datetime.now(tz=timezone.utc) + timedelta(hours=1)
    a_id = _make_feed(name="A", next_fetch_at=future)
    b_id = _make_feed(name="B", next_fetch_at=None)
    _make_feed(name="C", enabled=False)
    all_ids = {f.id for f in repo.list_due_feeds(force=True)}
    assert all_ids == {a_id, b_id}

  def test_excludes_disabled(self, clean_db):
    _make_feed(name="disabled", enabled=False, next_fetch_at=None)
    assert repo.list_due_feeds(force=False) == []


# -- toggle_feed enabled transition --


class TestToggleFeed:
  def test_disable_clears_next_fetch_at(self, clean_db):
    future = datetime.now(tz=timezone.utc) + timedelta(hours=1)
    fid = _make_feed(enabled=True, next_fetch_at=future)
    repo.toggle_feed(fid)
    row = _read_feed(fid)
    assert row["enabled"] is False
    assert row["next_fetch_at"] is None

  def test_enable_leaves_next_fetch_at(self, clean_db):
    future = datetime.now(tz=timezone.utc) + timedelta(hours=1)
    fid = _make_feed(enabled=False, next_fetch_at=future)
    repo.toggle_feed(fid)
    row = _read_feed(fid)
    assert row["enabled"] is True
    # false→true must not touch next_fetch_at.
    assert row["next_fetch_at"] is not None


# -- fetch_interval_min CHECK --


class TestBucketCheck:
  def test_rejects_non_bucket_value(self, clean_db):
    with pytest.raises(psycopg.errors.CheckViolation):
      with db.connection() as conn:
        conn.execute(
          "INSERT INTO feeds (name, url, fetch_interval_min) "
          "VALUES ('bad', 'https://bad.example.com/', 45)",
        )


# -- advisory_lock --


class TestAdvisoryLock:
  def test_lock_acquires_and_releases(self, clean_db):
    with repo.advisory_lock() as got:
      assert got is True
    # Second acquisition after release should succeed.
    with repo.advisory_lock() as got:
      assert got is True


# -- outcome classifier --


class TestClassifyOutcome:
  def test_fetch_exception_is_failure(self):
    outcome, error = _classify_outcome(
      source_tag="fresh", feed_kind="rss",
      fetch_exc=RuntimeError("boom"), persist_exc=None,
      inserted_count=0,
    )
    assert outcome == "failure"
    assert error is not None and "boom" in error

  def test_persist_exception_is_failure(self):
    outcome, _ = _classify_outcome(
      source_tag="fresh", feed_kind="rss",
      fetch_exc=None, persist_exc=RuntimeError("db"),
      inserted_count=0,
    )
    assert outcome == "failure"

  def test_web_norules_is_degraded_with_error(self):
    outcome, error = _classify_outcome(
      source_tag=None, feed_kind="web-norules",
      fetch_exc=None, persist_exc=None, inserted_count=0,
    )
    assert outcome == "degraded"
    assert error == "extraction rules not configured"

  def test_net_cache_is_degraded_with_diagnostic(self):
    # Serving a stale cached copy must surface a diagnostic so the feed does
    # not silently look healthy on old content.
    outcome, error = _classify_outcome(
      source_tag="net-cache", feed_kind="rss",
      fetch_exc=None, persist_exc=None, inserted_count=0,
    )
    assert outcome == "degraded"
    assert error is not None and "network error" in error

  def test_5xx_cache_is_degraded(self):
    outcome, error = _classify_outcome(
      source_tag="5xx-cache", feed_kind="rss",
      fetch_exc=None, persist_exc=None, inserted_count=0,
    )
    assert outcome == "degraded"
    assert error is not None and "stale cache" in error

  def test_304_empty_is_degraded(self):
    outcome, error = _classify_outcome(
      source_tag="304-empty", feed_kind="rss",
      fetch_exc=None, persist_exc=None, inserted_count=0,
    )
    assert outcome == "degraded"
    assert error is not None and "304" in error

  def test_fresh_with_inserts(self):
    outcome, _ = _classify_outcome(
      source_tag="fresh", feed_kind="rss",
      fetch_exc=None, persist_exc=None, inserted_count=3,
    )
    assert outcome == "fresh"

  def test_fresh_with_zero_inserts_is_miss(self):
    outcome, _ = _classify_outcome(
      source_tag="fresh", feed_kind="rss",
      fetch_exc=None, persist_exc=None, inserted_count=0,
    )
    assert outcome == "miss"

  def test_web_feed_success(self):
    # Web feeds have source_tag=None but feed_kind="web".
    outcome, _ = _classify_outcome(
      source_tag=None, feed_kind="web",
      fetch_exc=None, persist_exc=None, inserted_count=2,
    )
    assert outcome == "fresh"
