"""Behavioral tests for POST /api/feeds/{id}/rules/infer.

The endpoint must: reject non-web feeds (400), refuse when the LLM is disabled
(503), and — most importantly — NEVER persist the inferred rules (it only
returns a preview). External I/O (LLM probe, page fetch, LLM call) is mocked so
the test runs without a live network or Ollama.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

import app as app_module
import db
import selector_inference
from schemas import AppConfig

pytestmark = pytest.mark.usefixtures("clean_db")

LIST_HTML = """
<html><body>
<ul><li class="entry"><a class="t" href="/a/1">First</a></li>
<li class="entry"><a class="t" href="/a/2">Second</a></li></ul>
</body></html>
"""

GOOD_RULES = {"item": "li.entry", "title": "a.t", "link": "a.t"}


def _insert_feed(*, web: bool) -> int:
  rules = "'{}'" if web else "NULL"
  with db.connection() as conn:
    row = conn.execute(
      f"""INSERT INTO feeds (name, url, translate, summarize, enabled, extraction_rules)
          VALUES ('Web', 'https://example.com/', FALSE, FALSE, TRUE, {rules})
          RETURNING id"""
    ).fetchone()
  assert row is not None
  return int(row["id"])


def _stored_rules(feed_id: int) -> str | None:
  with db.connection() as conn:
    row = conn.execute(
      "SELECT extraction_rules FROM feeds WHERE id = %s", (feed_id,)
    ).fetchone()
  assert row is not None
  return row["extraction_rules"]


def _client(config: AppConfig | None = None) -> TestClient:
  app_module.app.state.config = config or AppConfig()
  return TestClient(app_module.app)


class _FakeResp:
  def __init__(self, html: str) -> None:
    self.content = html.encode("utf-8")
    self.encoding = "utf-8"
    self.url = "https://example.com/"

  def raise_for_status(self) -> None:
    pass


def _mock_inference(monkeypatch: pytest.MonkeyPatch, rules: dict[str, Any]) -> None:
  """Patch the LLM probe, page fetch, and LLM call for a happy-path inference."""
  monkeypatch.setattr(app_module, "_probe_llm", lambda _url: (True, None))

  async def fake_get(_self: Any, _url: str, **_kw: Any) -> _FakeResp:
    return _FakeResp(LIST_HTML)

  monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

  async def fake_ollama(*_a: Any, **_kw: Any) -> dict[str, Any]:
    return rules

  monkeypatch.setattr(selector_inference, "call_ollama", fake_ollama)


def test_non_web_feed_rejected() -> None:
  feed_id = _insert_feed(web=False)
  resp = _client().post(f"/api/feeds/{feed_id}/rules/infer")
  assert resp.status_code == 400


def test_llm_disabled_returns_503() -> None:
  feed_id = _insert_feed(web=True)
  config = AppConfig()
  config.ollama.enabled = False
  resp = _client(config).post(f"/api/feeds/{feed_id}/rules/infer")
  assert resp.status_code == 503
  assert _stored_rules(feed_id) == "{}"  # unchanged


def test_inference_returns_preview_without_persisting(monkeypatch: pytest.MonkeyPatch) -> None:
  feed_id = _insert_feed(web=True)
  _mock_inference(monkeypatch, GOOD_RULES)
  # The fixture page lists exactly two articles, so accept a two-item match.
  config = AppConfig()
  config.selector_inference.min_articles = 2
  resp = _client(config).post(f"/api/feeds/{feed_id}/rules/infer")
  assert resp.status_code == 200
  body = resp.json()
  assert body["status"] == "ok"
  assert body["rules"] == GOOD_RULES
  assert [a["title"] for a in body["sample_articles"]] == ["First", "Second"]
  # The whole point: a preview must not write to the DB.
  assert _stored_rules(feed_id) == "{}"
