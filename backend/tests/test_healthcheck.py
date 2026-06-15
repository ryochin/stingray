"""Tests for healthcheck.py: config loading, LLM gating, and job-age logic.

The _check_recent_job branch (covered below):
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


def _run_main(monkeypatch, *, enabled: bool, argv: list[str]) -> tuple[int, dict[str, int]]:
  """Run healthcheck.main() with all checks stubbed, counting each check call."""
  calls = {"db": 0, "job": 0, "llm": 0}
  monkeypatch.setattr(healthcheck, "_check_db", lambda: calls.update(db=calls["db"] + 1))
  monkeypatch.setattr(healthcheck, "_check_recent_job", lambda max_age_minutes: calls.update(job=calls["job"] + 1))
  monkeypatch.setattr(healthcheck, "_check_llm", lambda base_url, timeout: calls.update(llm=calls["llm"] + 1))
  monkeypatch.setattr(healthcheck, "_load_config", lambda: AppConfig(ollama=OllamaConfig(enabled=enabled)))
  monkeypatch.setattr("sys.argv", ["healthcheck.py", *argv])
  return healthcheck.main(), calls


class TestLlmGating:
  def test_llm_checked_when_enabled(self, monkeypatch):
    code, calls = _run_main(monkeypatch, enabled=True, argv=[])
    assert code == 0
    assert calls["llm"] == 1

  def test_llm_skipped_when_config_disabled(self, monkeypatch):
    code, calls = _run_main(monkeypatch, enabled=False, argv=[])
    assert code == 0
    assert calls["llm"] == 0
    # Disabling the LLM must not skip the db/job checks.
    assert calls["db"] == 1 and calls["job"] == 1

  def test_skip_llm_flag_does_not_read_config(self, monkeypatch):
    # --skip-llm-check must short-circuit before _load_config is ever touched.
    llm_calls: list[str] = []
    monkeypatch.setattr(healthcheck, "_check_db", lambda: None)
    monkeypatch.setattr(healthcheck, "_check_recent_job", lambda max_age_minutes: None)
    monkeypatch.setattr(healthcheck, "_check_llm", lambda base_url, timeout: llm_calls.append(base_url))

    def _fail() -> AppConfig:
      raise AssertionError("_load_config must not be called with --skip-llm-check")

    monkeypatch.setattr(healthcheck, "_load_config", _fail)
    monkeypatch.setattr("sys.argv", ["healthcheck.py", "--skip-llm-check"])
    assert healthcheck.main() == 0
    assert llm_calls == []

  def test_config_load_error_fails_without_running_llm(self, monkeypatch, capsys):
    llm_calls: list[str] = []
    monkeypatch.setattr(healthcheck, "_check_db", lambda: None)
    monkeypatch.setattr(healthcheck, "_check_recent_job", lambda max_age_minutes: None)
    monkeypatch.setattr(healthcheck, "_check_llm", lambda base_url, timeout: llm_calls.append(base_url))

    def _boom() -> AppConfig:
      raise ValueError("bad config")

    monkeypatch.setattr(healthcheck, "_load_config", _boom)
    monkeypatch.setattr("sys.argv", ["healthcheck.py"])
    assert healthcheck.main() == 1
    assert llm_calls == []
    err = capsys.readouterr().err
    assert "healthcheck: config:" in err
    assert "bad config" in err


class TestLoadConfig:
  def test_reads_ollama_enabled_false(self, tmp_path, monkeypatch):
    (tmp_path / "config.yml").write_text("ollama:\n  enabled: false\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert healthcheck._load_config().ollama.enabled is False

  def test_missing_config_defaults_enabled_true(self, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert healthcheck._load_config().ollama.enabled is True

  def test_non_mapping_yaml_is_rejected(self, tmp_path, monkeypatch):
    # A config the fetcher (main.py) would reject must not be silently accepted.
    (tmp_path / "config.yml").write_text("just a string\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="not a valid YAML mapping"):
      healthcheck._load_config()

  def test_empty_yaml_is_rejected(self, tmp_path, monkeypatch):
    (tmp_path / "config.yml").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="not a valid YAML mapping"):
      healthcheck._load_config()
