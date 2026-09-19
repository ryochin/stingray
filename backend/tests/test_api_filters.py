"""Behavioral tests for the filter endpoints and their duplicate handling.

Locks the contract the Filters screen relies on: an equivalent rule is refused
with 409 and a string `detail` (the shape `fetchJson` turns into a message),
and an import counts equivalent rules as skipped rather than inserting them.

The TestClient is built without the lifespan context manager so no background
tasks or DB probe run; the DB is provided by the `clean_db` fixture.
"""

from __future__ import annotations

import io
import json

import pytest
from fastapi.testclient import TestClient

import app as app_module
import repo

pytestmark = pytest.mark.usefixtures("clean_db")


def _client() -> TestClient:
  return TestClient(app_module.app)


def _import(client: TestClient, data: object) -> dict[str, int]:
  payload = io.BytesIO(json.dumps(data).encode("utf-8"))
  resp = client.post(
    "/api/filters/import",
    files={"file": ("filters.json", payload, "application/json")},
  )
  assert resp.status_code == 200
  return resp.json()


class TestCreateFilter:
  def test_creates_and_trims(self):
    resp = _client().post("/api/filters", json={"pattern": "  spam  "})
    assert resp.status_code == 201
    assert resp.json()["pattern"] == "spam"
    assert resp.json()["target"] == "title"

  def test_equivalent_pattern_conflicts(self):
    client = _client()
    assert client.post("/api/filters", json={"pattern": "spam"}).status_code == 201
    resp = client.post("/api/filters", json={"pattern": "SPAM"})
    assert resp.status_code == 409
    assert resp.json()["detail"] == 'Filter "SPAM" already exists'
    assert len(repo.list_filters()) == 1

  def test_same_pattern_on_another_target_is_created(self):
    client = _client()
    client.post("/api/filters", json={"pattern": "spam", "target": "title"})
    resp = client.post("/api/filters", json={"pattern": "spam", "target": "both"})
    assert resp.status_code == 201

  def test_blank_pattern_is_rejected(self):
    assert _client().post("/api/filters", json={"pattern": ""}).status_code == 422
    assert _client().post("/api/filters", json={"pattern": "   "}).status_code == 400

  def test_unknown_target_is_rejected(self):
    resp = _client().post("/api/filters", json={"pattern": "spam", "target": "url"})
    assert resp.status_code == 422


class TestImportFilters:
  def test_skips_equivalent_existing_rules(self):
    client = _client()
    repo.add_filter("spam")
    result = _import(client, [{"pattern": " SPAM "}, {"pattern": "ham"}])
    assert result == {"created": 1, "skipped": 1}
    assert len(repo.list_filters()) == 2

  def test_skips_equivalent_rules_within_one_file(self):
    result = _import(_client(), [{"pattern": "spam"}, {"pattern": "Spam"}])
    assert result == {"created": 1, "skipped": 1}
