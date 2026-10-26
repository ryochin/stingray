"""GET /api/health backs the web container's healthcheck: a DB failure must
surface as 503 with the cause, not as a 500 or a false "ok"."""

from __future__ import annotations

from contextlib import AbstractContextManager

import pytest
from fastapi.testclient import TestClient

import app as app_module


def _client() -> TestClient:
  return TestClient(app_module.app)


@pytest.mark.usefixtures("db_session")
def test_reachable_db_is_ok() -> None:
  resp = _client().get("/api/health")
  assert resp.status_code == 200
  assert resp.json() == {"status": "ok"}


def test_db_failure_is_503_with_the_cause(monkeypatch: pytest.MonkeyPatch) -> None:
  def broken_connection() -> AbstractContextManager[object]:
    raise RuntimeError("down")

  monkeypatch.setattr(app_module.db, "connection", broken_connection)
  resp = _client().get("/api/health")
  assert resp.status_code == 503
  assert resp.json() == {"detail": "db: RuntimeError: down"}
