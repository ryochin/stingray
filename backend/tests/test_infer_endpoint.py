"""Behavioral tests for POST /api/feeds/{id}/rules/infer.

The endpoint must: reject non-web feeds (400), refuse when the LLM is disabled
(503), and — most importantly — NEVER persist the inferred rules (it only
returns a preview). External I/O (LLM probe, page fetch, LLM call) is mocked so
the test runs without a live network or Ollama.
"""

from __future__ import annotations

from functools import partial
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

import app as app_module
import db
import selector_inference
from safe_http import SafeClient
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


def _mock_inference(monkeypatch: pytest.MonkeyPatch, rules: dict[str, Any]) -> None:
  """Patch the LLM probe, page fetch, and LLM call for a happy-path inference."""
  monkeypatch.setattr(app_module, "_probe_llm", lambda _url: (True, None))

  async def public_dns(_host: str, _port: int) -> list[str]:
    return ["93.184.216.34"]

  transport = httpx.MockTransport(
    lambda _req: httpx.Response(200, html=LIST_HTML)
  )
  monkeypatch.setattr(
    app_module, "SafeClient", partial(SafeClient, transport=transport, resolver=public_dns),
  )

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


def test_unresolvable_page_returns_502(monkeypatch: pytest.MonkeyPatch) -> None:
  feed_id = _insert_feed(web=True)
  monkeypatch.setattr(app_module, "_probe_llm", lambda _url: (True, None))

  async def no_dns(_host: str, _port: int) -> list[str]:
    raise OSError("name resolution failed")

  monkeypatch.setattr(app_module, "SafeClient", partial(SafeClient, resolver=no_dns))
  resp = _client().post(f"/api/feeds/{feed_id}/rules/infer")
  assert resp.status_code == 502


class _Captured(Exception):
  pass


def test_page_is_truncated_at_5_mib_whatever_the_fetch_limit(monkeypatch: pytest.MonkeyPatch) -> None:
  feed_id = _insert_feed(web=True)
  monkeypatch.setattr(app_module, "_probe_llm", lambda _url: (True, None))
  served: list[int] = []

  async def huge_page() -> Any:
    for _ in range(12):  # 6 MiB in 512 KiB chunks, no Content-Length
      served.append(1)
      yield b"x" * (512 * 1024)

  async def public_dns(_host: str, _port: int) -> list[str]:
    return ["93.184.216.34"]

  transport = httpx.MockTransport(lambda _req: httpx.Response(200, content=huge_page()))
  monkeypatch.setattr(
    app_module, "SafeClient", partial(SafeClient, transport=transport, resolver=public_dns),
  )
  seen: list[int] = []

  async def capture(_client: Any, _model: str, html: str, **_kw: Any) -> Any:
    seen.append(len(html))
    raise _Captured

  monkeypatch.setattr(app_module, "infer_and_validate", capture)
  # A fetch limit far below 5 MiB must not turn the page into an error.
  config = AppConfig()
  config.fetch.max_response_bytes = 64 * 1024
  with pytest.raises(_Captured):
    _client(config).post(f"/api/feeds/{feed_id}/rules/infer")
  assert seen == [5 * 1024 * 1024]
  assert len(served) == 10
