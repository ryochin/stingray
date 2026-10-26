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

import httpx
import pytest
from fastapi.testclient import TestClient

import app as app_module
import repo

pytestmark = pytest.mark.usefixtures("clean_db")


def _client() -> TestClient:
  return TestClient(app_module.app)


def _post_import(client: TestClient, raw: bytes) -> httpx.Response:
  """POST raw bytes as the import file and return the response unchecked."""
  return client.post(
    "/api/filters/import",
    files={"file": ("filters.json", io.BytesIO(raw), "application/json")},
  )


def _import(client: TestClient, data: object) -> dict[str, int]:
  resp = _post_import(client, json.dumps(data).encode("utf-8"))
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

  def test_url_target_is_accepted(self):
    resp = _client().post("/api/filters", json={"pattern": "/sponsored/", "target": "url"})
    assert resp.status_code == 201
    assert resp.json()["target"] == "url"

  def test_blank_pattern_is_rejected(self):
    assert _client().post("/api/filters", json={"pattern": ""}).status_code == 422
    assert _client().post("/api/filters", json={"pattern": "   "}).status_code == 400

  def test_unknown_target_is_rejected(self):
    resp = _client().post("/api/filters", json={"pattern": "spam", "target": "author"})
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

  def test_accepts_url_target(self):
    result = _import(_client(), [{"pattern": "spam", "target": "url"}])
    assert result == {"created": 1, "skipped": 0}
    assert [f.target for f in repo.list_filters()] == ["url"]

  @pytest.mark.parametrize("raw", [b"\xff\xfe", b"{"], ids=["not-utf8", "broken-json"])
  def test_undecodable_file_is_rejected(self, raw: bytes):
    resp = _post_import(_client(), raw)
    assert resp.status_code == 400
    assert resp.json()["detail"].startswith("Invalid JSON")
    assert repo.list_filters() == []

  def test_non_array_is_rejected(self):
    resp = _post_import(_client(), json.dumps({"pattern": "x"}).encode("utf-8"))
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Expected a JSON array"
    assert repo.list_filters() == []

  def test_malformed_entries_are_dropped_uncounted(self):
    data: list[object] = [
      1,
      "spam",
      {"target": "title"},
      {"pattern": "   "},
      {"pattern": "a", "target": "author"},
      {"pattern": "ok"},
    ]
    assert _import(_client(), data) == {"created": 1, "skipped": 0}
    assert [(f.pattern, f.target) for f in repo.list_filters()] == [("ok", "title")]

  def test_target_is_trimmed(self):
    assert _import(_client(), [{"pattern": "spam", "target": " url "}]) == {
      "created": 1,
      "skipped": 0,
    }
    assert [f.target for f in repo.list_filters()] == ["url"]

  def test_non_string_pattern_is_stored_as_text(self):
    # Pinned behavior: a non-string pattern is coerced with str(), not dropped.
    assert _import(_client(), [{"pattern": 123}]) == {"created": 1, "skipped": 0}
    assert [f.pattern for f in repo.list_filters()] == ["123"]


class TestExportFilters:
  def test_round_trips_through_import(self):
    client = _client()
    repo.add_filter("spam", "title")
    repo.add_filter("/sponsored/", "url")
    repo.add_filter("広告", "both")
    original = [(f.pattern, f.target) for f in repo.list_filters()]

    resp = client.get("/api/filters/export")
    assert resp.status_code == 200
    assert "filters.json" in resp.headers["content-disposition"]
    exported = resp.content

    for f in repo.list_filters():
      repo.delete_filter(f.id)
    assert repo.list_filters() == []

    assert _post_import(client, exported).json() == {"created": 3, "skipped": 0}
    assert [(f.pattern, f.target) for f in repo.list_filters()] == original
    assert _post_import(client, exported).json() == {"created": 0, "skipped": 3}
