"""Container healthcheck for the fetcher (and reusable from web).

Checks:
  1. Database reachable (SELECT 1).
  2. LLM backend (Ollama) reachable at OLLAMA_BASE_URL.
  3. Latest refresh_jobs.finished_at is recent (within MAX_AGE_MINUTES).
     If no job record exists yet, treated as a startup grace period and passes.

Exit 0 = healthy, non-zero = unhealthy. Diagnostic output goes to stderr.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

import httpx

import db
import repo


def _check_db() -> None:
  db.configure()
  with db.connection() as conn:
    conn.execute("SELECT 1").fetchone()


def _check_llm(base_url: str, timeout: float) -> None:
  with httpx.Client(timeout=timeout) as client:
    resp = client.get(f"{base_url.rstrip('/')}/api/tags")
    resp.raise_for_status()


def _check_recent_job(max_age_minutes: int) -> None:
  job = repo.get_latest_refresh_job()
  if job is None:
    return  # Startup grace: no job has run yet.
  if job.finished_at is None:
    # A job is currently running; accept as healthy unless it has been running absurdly long.
    age = datetime.now(tz=timezone.utc) - job.started_at
    if age > timedelta(minutes=max_age_minutes * 2):
      raise RuntimeError(f"Job {job.id} has been running for {age}.")
    return
  age = datetime.now(tz=timezone.utc) - job.finished_at
  if age > timedelta(minutes=max_age_minutes):
    raise RuntimeError(
      f"Last job finished {age} ago (> {max_age_minutes}m).",
    )


def main() -> int:
  parser = argparse.ArgumentParser()
  parser.add_argument("--max-age-minutes", type=int, default=45)
  parser.add_argument("--llm-timeout", type=float, default=5.0)
  parser.add_argument("--skip-job-check", action="store_true", help="Skip the refresh_jobs age check (use for web).")
  parser.add_argument("--skip-llm-check", action="store_true", help="Skip the LLM reachability check (use for web).")
  args = parser.parse_args()

  base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

  try:
    _check_db()
  except Exception as e:
    print(f"healthcheck: db: {type(e).__name__}: {e}", file=sys.stderr)
    return 1

  if not args.skip_llm_check:
    try:
      _check_llm(base_url, timeout=args.llm_timeout)
    except Exception as e:
      print(f"healthcheck: llm ({base_url}): {type(e).__name__}: {e}", file=sys.stderr)
      return 1

  if not args.skip_job_check:
    try:
      _check_recent_job(args.max_age_minutes)
    except Exception as e:
      print(f"healthcheck: job: {e}", file=sys.stderr)
      return 1

  return 0


if __name__ == "__main__":
  sys.exit(main())
