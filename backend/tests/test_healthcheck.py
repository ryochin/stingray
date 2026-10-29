"""Tests for healthcheck.py: LLM gating and job-age logic.

The _check_recent_job branch (covered below):
  - no job record → startup grace (OK)
  - job running but within 2× max_age → OK
  - job running and exceeded 2× max_age → fail
  - finished job within max_age → OK
  - finished job older than max_age → fail
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

import db
import healthcheck
from schemas import AppConfig, OllamaConfig


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


class TestCheckRecentJob:
  pytestmark = pytest.mark.usefixtures("clean_db")

  def test_no_job_is_healthy_startup_grace(self):
    # No refresh_jobs row → container is considered healthy (fresh boot).
    healthcheck._check_recent_job(max_age_minutes=45)

  def test_running_job_within_grace_is_healthy(self):
    now = datetime.now(tz=UTC)
    _insert_job(started_at=now - timedelta(minutes=10), finished_at=None)
    healthcheck._check_recent_job(max_age_minutes=45)

  def test_long_running_job_fails(self):
    # Job running for > 2× max_age_minutes → considered stuck.
    now = datetime.now(tz=UTC)
    _insert_job(started_at=now - timedelta(minutes=200), finished_at=None)
    with pytest.raises(RuntimeError, match="has been running"):
      healthcheck._check_recent_job(max_age_minutes=45)

  def test_finished_recent_is_healthy(self):
    now = datetime.now(tz=UTC)
    _insert_job(
      started_at=now - timedelta(minutes=20),
      finished_at=now - timedelta(minutes=5),
      status="success",
    )
    healthcheck._check_recent_job(max_age_minutes=45)

  def test_finished_stale_fails(self):
    now = datetime.now(tz=UTC)
    _insert_job(
      started_at=now - timedelta(hours=3),
      finished_at=now - timedelta(hours=2),
      status="success",
    )
    with pytest.raises(RuntimeError, match="Last job finished"):
      healthcheck._check_recent_job(max_age_minutes=45)


def _run_main(
  monkeypatch: pytest.MonkeyPatch, *, enabled: bool, argv: list[str], fail: str | None = None
) -> tuple[int, dict[str, int]]:
  """Run healthcheck.main() with all checks stubbed, counting each check call.

  `fail` names the check ("db", "llm" or "job") whose stub raises after counting.
  """
  calls = {"db": 0, "job": 0, "llm": 0}

  def _record(name: str) -> None:
    calls[name] += 1
    if name == fail:
      raise RuntimeError(f"{name} down")

  def _check_recent_job(max_age_minutes: int) -> None:
    _record("job")

  def _check_llm(base_url: str, timeout: float) -> None:
    _record("llm")

  monkeypatch.setattr(healthcheck, "_check_db", lambda: _record("db"))
  monkeypatch.setattr(healthcheck, "_check_recent_job", _check_recent_job)
  monkeypatch.setattr(healthcheck, "_check_llm", _check_llm)
  monkeypatch.setattr(
    healthcheck, "_load_config", lambda: AppConfig(ollama=OllamaConfig(enabled=enabled))
  )
  monkeypatch.setattr("sys.argv", ["healthcheck.py", *argv])
  return healthcheck.main(), calls


class TestLlmGating:
  def test_llm_checked_when_enabled(self, monkeypatch: pytest.MonkeyPatch):
    code, calls = _run_main(monkeypatch, enabled=True, argv=[])
    assert code == 0
    assert calls["llm"] == 1

  def test_llm_skipped_when_config_disabled(self, monkeypatch: pytest.MonkeyPatch):
    code, calls = _run_main(monkeypatch, enabled=False, argv=[])
    assert code == 0
    assert calls["llm"] == 0
    # Disabling the LLM must not skip the db/job checks.
    assert calls["db"] == 1 and calls["job"] == 1

  def test_skip_llm_flag_does_not_read_config(self, monkeypatch: pytest.MonkeyPatch):
    # --skip-llm-check must short-circuit before _load_config is ever touched.
    llm_calls: list[str] = []

    def _check_recent_job(max_age_minutes: int) -> None:
      pass

    def _check_llm(base_url: str, timeout: float) -> None:
      llm_calls.append(base_url)

    monkeypatch.setattr(healthcheck, "_check_db", lambda: None)
    monkeypatch.setattr(healthcheck, "_check_recent_job", _check_recent_job)
    monkeypatch.setattr(healthcheck, "_check_llm", _check_llm)

    def _fail() -> AppConfig:
      raise AssertionError("_load_config must not be called with --skip-llm-check")

    monkeypatch.setattr(healthcheck, "_load_config", _fail)
    monkeypatch.setattr("sys.argv", ["healthcheck.py", "--skip-llm-check"])
    assert healthcheck.main() == 0
    assert llm_calls == []

  def test_config_load_error_fails_without_running_llm(
    self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
  ):
    llm_calls: list[str] = []

    def _check_recent_job(max_age_minutes: int) -> None:
      pass

    def _check_llm(base_url: str, timeout: float) -> None:
      llm_calls.append(base_url)

    monkeypatch.setattr(healthcheck, "_check_db", lambda: None)
    monkeypatch.setattr(healthcheck, "_check_recent_job", _check_recent_job)
    monkeypatch.setattr(healthcheck, "_check_llm", _check_llm)

    def _boom() -> AppConfig:
      raise ValueError("bad config")

    monkeypatch.setattr(healthcheck, "_load_config", _boom)
    monkeypatch.setattr("sys.argv", ["healthcheck.py"])
    assert healthcheck.main() == 1
    assert llm_calls == []
    err = capsys.readouterr().err
    assert "healthcheck: config:" in err
    assert "bad config" in err


class TestMainFailures:
  @pytest.mark.parametrize(
    ("fail", "prefix", "expected_calls"),
    [
      # A db failure stops before the llm/job checks run.
      ("db", "healthcheck: db: ", {"db": 1, "llm": 0, "job": 0}),
      ("llm", "healthcheck: llm (http://ollama.test:11434): ", {"db": 1, "llm": 1, "job": 0}),
      ("job", "healthcheck: job: ", {"db": 1, "llm": 1, "job": 1}),
    ],
  )
  def test_failed_check_returns_1_with_prefix(
    self,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fail: str,
    prefix: str,
    expected_calls: dict[str, int],
  ):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.test:11434")
    code, calls = _run_main(monkeypatch, enabled=True, argv=[], fail=fail)
    assert code == 1
    assert calls == expected_calls
    err = capsys.readouterr().err
    assert err.startswith(prefix)
    assert f"{fail} down" in err

  def test_skip_job_check_does_not_run_job_check(self, monkeypatch: pytest.MonkeyPatch):
    code, calls = _run_main(monkeypatch, enabled=True, argv=["--skip-job-check"], fail="job")
    assert code == 0
    assert calls == {"db": 1, "llm": 1, "job": 0}
