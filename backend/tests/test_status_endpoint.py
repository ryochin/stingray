"""Behavioral tests for GET /api/status: the refresh-job state and the LLM probe.

The frontend polls this to show "Fetching..." and the LLM badge, so `running`
must follow the latest job rather than stick at one value. _probe_llm is
replaced so no request reaches Ollama.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

import app as app_module
import db
import repo
from schemas import AppConfig

pytestmark = pytest.mark.usefixtures("clean_db")

STARTED = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
FINISHED = datetime(2026, 1, 2, 3, 9, 0, tzinfo=UTC)


def _get_status(config: AppConfig) -> dict[str, object]:
  app_module.app.state.config = config
  resp = TestClient(app_module.app).get("/api/status")
  assert resp.status_code == 200
  return resp.json()


def test_disabled_llm_is_not_probed_and_no_job_is_not_running(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  probed: list[str] = []

  def probe(url: str) -> tuple[bool, str | None]:
    probed.append(url)
    return True, None

  monkeypatch.setattr(app_module, "_probe_llm", probe)
  config = AppConfig()
  config.ollama.enabled = False
  body = _get_status(config)
  assert probed == []
  assert body["running"] is False
  assert body["last_status"] is None
  assert (body["llm_enabled"], body["llm_available"], body["llm_error"]) == (False, False, None)


@pytest.mark.parametrize(
  ("status", "error"),
  [("running", None), ("done", None), ("failed", "boom")],
)
def test_latest_job_is_reported(
  monkeypatch: pytest.MonkeyPatch, status: str, error: str | None
) -> None:
  def probe(_url: str) -> tuple[bool, str | None]:
    return False, "offline"

  monkeypatch.setattr(app_module, "_probe_llm", probe)
  repo.finish_refresh_job(repo.create_refresh_job("cron"), "done", 1, None)
  job_id = repo.create_refresh_job("web")
  if status != "running":
    repo.finish_refresh_job(job_id, status, 7, error)
  # Distinct fixed times, so swapped or mis-wired timestamps are caught.
  with db.connection() as conn:
    conn.execute(
      """UPDATE refresh_jobs
         SET started_at = %s, finished_at = CASE WHEN finished_at IS NULL THEN NULL ELSE %s END
         WHERE id = %s""",
      (STARTED, FINISHED, job_id),
    )
  body = _get_status(AppConfig())
  assert body["running"] is (status == "running")
  assert body["last_status"] == status
  assert datetime.fromisoformat(str(body["last_started_at"])) == STARTED
  finished = body["last_finished_at"]
  if status == "running":
    assert finished is None
  else:
    assert datetime.fromisoformat(str(finished)) == FINISHED
  assert body["last_new_count"] == (None if status == "running" else 7)
  assert body["last_error"] == error
  assert (body["llm_enabled"], body["llm_available"], body["llm_error"]) == (True, False, "offline")
