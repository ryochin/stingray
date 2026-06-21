"""Tests for selector_inference: HTML preprocessing and infer-and-validate.

call_ollama is monkeypatched so these run without a live LLM. parse_web_page is
used for real, so the validation loop is exercised end-to-end against fixtures.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

import selector_inference
from selector_inference import infer_and_validate, preprocess_html

LIST_HTML = """
<html><head><style>.x{color:red}</style></head><body>
<script>var a = 1;</script>
<ul class="list">
  <li class="entry" style="margin:0" onclick="x()">
    <a class="t" href="/a/1">First</a>
    <time datetime="2026-01-01">Jan 1</time>
  </li>
  <li class="entry">
    <a class="t" href="/a/2">Second</a>
    <time datetime="2026-01-02">Jan 2</time>
  </li>
</ul>
</body></html>
"""

GOOD_RULES = {"item": "li.entry", "title": "a.t", "link": "a.t", "link_attr": "href"}
ZERO_RULES = {"item": "div.nope", "title": "a", "link": "a"}
ONE_MATCH_RULES = {"item": "li.entry:first-child", "title": "a.t", "link": "a.t"}

# A page exposing both a tag/index side-list and the real article list, used to
# exercise the quality scoring: the tag list meets the count but its links are
# category-index URLs, so a clean post list should win.
COMBINED_HTML = """
<html><body>
<ul class="tags">
  <li class="t"><a href="/tag/python">Python</a></li>
  <li class="t"><a href="/tag/golang">Go</a></li>
  <li class="t"><a href="/tag/rust">Rust</a></li>
</ul>
<ul class="posts">
  <li class="p"><a href="/post/1">Post 1</a></li>
  <li class="p"><a href="/post/2">Post 2</a></li>
  <li class="p"><a href="/post/3">Post 3</a></li>
</ul>
</body></html>
"""
TAG_RULES = {"item": "li.t", "title": "a", "link": "a", "link_attr": "href"}
POST_RULES = {"item": "li.p", "title": "a", "link": "a", "link_attr": "href"}
# A clean but below-count match (1 article) on the combined page.
ONE_POST_RULES = {"item": "li.p:first-child", "title": "a", "link": "a", "link_attr": "href"}


class TestPreprocessHtml:
  def test_strips_script_and_style(self):
    out = preprocess_html(LIST_HTML, max_bytes=100_000)
    assert "<script" not in out
    assert "<style" not in out
    assert "var a = 1" not in out

  def test_keeps_structural_attrs_drops_noise(self):
    out = preprocess_html(LIST_HTML, max_bytes=100_000)
    assert 'class="entry"' in out  # structural attr kept
    assert "datetime=" in out
    assert "onclick" not in out  # event handler stripped
    assert "margin:0" not in out  # inline style stripped

  def test_truncates_to_max_bytes(self):
    out = preprocess_html(LIST_HTML, max_bytes=50)
    assert len(out.encode("utf-8")) <= 50

  def test_strips_layout_noise_and_carousels(self):
    html = (
      "<body><nav>menu</nav><aside>side</aside>"
      '<div class="swiper">slide</div>'
      '<ul class="list"><li class="entry"><header><a>x</a></header></li></ul>'
      "<footer>foot</footer></body>"
    )
    out = preprocess_html(html, max_bytes=100_000)
    assert "<nav" not in out
    assert "<aside" not in out
    assert "<footer" not in out
    assert "swiper" not in out
    assert 'class="entry"' in out  # the real list survives
    # <header> is kept: article cards commonly nest their title/link in one.
    assert "<header" in out


def _fake_ollama(responses: list[dict[str, Any]]):
  """Return an async stand-in for call_ollama yielding queued responses."""
  calls = {"n": 0}

  async def fake(
    _client: Any, _model: str, _system: str, _user: str, *, options: Any = None
  ) -> dict[str, Any]:
    idx = min(calls["n"], len(responses) - 1)
    calls["n"] += 1
    return responses[idx]

  return fake, calls


def _run_infer(html: str = LIST_HTML, **overrides: Any) -> selector_inference.InferResult:
  kwargs: dict[str, Any] = dict(
    page_url="https://example.com/",
    source="Example",
    max_html_bytes=100_000,
    max_attempts=2,
  )
  kwargs.update(overrides)
  return asyncio.run(
    infer_and_validate(None, "test-model", html, **kwargs)  # type: ignore[arg-type]
  )


class TestInferAndValidate:
  def test_success_first_try(self, monkeypatch: pytest.MonkeyPatch):
    fake, calls = _fake_ollama([GOOD_RULES])
    monkeypatch.setattr(selector_inference, "call_ollama", fake)
    result = _run_infer()
    assert result.status == "ok"
    assert result.attempts == 1
    assert calls["n"] == 1
    assert len(result.sample_articles) == 2
    assert result.sample_articles[0].title == "First"
    assert result.sample_articles[0].url == "https://example.com/a/1"

  def test_retries_after_zero_matches(self, monkeypatch: pytest.MonkeyPatch):
    fake, calls = _fake_ollama([ZERO_RULES, GOOD_RULES])
    monkeypatch.setattr(selector_inference, "call_ollama", fake)
    result = _run_infer()
    assert result.status == "ok"
    assert result.attempts == 2
    assert calls["n"] == 2
    assert len(result.sample_articles) == 2

  def test_all_attempts_invalid_falls_back(self, monkeypatch: pytest.MonkeyPatch):
    # Missing the required "link" field every time -> validation fails.
    fake, _ = _fake_ollama([{"item": "li.entry", "title": "a.t"}])
    monkeypatch.setattr(selector_inference, "call_ollama", fake)
    result = _run_infer()
    assert result.status == "invalid"
    assert result.sample_articles == []

  def test_zero_matches_exhausts_attempts(self, monkeypatch: pytest.MonkeyPatch):
    fake, _ = _fake_ollama([ZERO_RULES])
    monkeypatch.setattr(selector_inference, "call_ollama", fake)
    result = _run_infer()
    assert result.status == "empty"
    assert result.sample_articles == []

  def test_single_match_retries_then_accepts(self, monkeypatch: pytest.MonkeyPatch):
    # 1 match is below min_articles (2), so it retries and accepts the 2-match try.
    fake, calls = _fake_ollama([ONE_MATCH_RULES, GOOD_RULES])
    monkeypatch.setattr(selector_inference, "call_ollama", fake)
    result = _run_infer()
    assert result.status == "ok"
    assert result.attempts == 2
    assert len(result.sample_articles) == 2

  def test_single_match_falls_back_as_empty_with_sample(self, monkeypatch: pytest.MonkeyPatch):
    # Only ever 1 match: below the threshold, but returned as a starting point.
    fake, _ = _fake_ollama([ONE_MATCH_RULES])
    monkeypatch.setattr(selector_inference, "call_ollama", fake)
    result = _run_infer()
    assert result.status == "empty"
    assert len(result.sample_articles) == 1

  def test_count_meeting_index_list_is_skipped_for_clean_list(
    self, monkeypatch: pytest.MonkeyPatch
  ):
    # The tag list meets the count but its links are /tag/ index URLs, so it is
    # not accepted early; the next try's clean /post/ list wins instead.
    fake, calls = _fake_ollama([TAG_RULES, POST_RULES])
    monkeypatch.setattr(selector_inference, "call_ollama", fake)
    result = _run_infer(html=COMBINED_HTML, max_attempts=3)
    assert result.status == "ok"
    assert result.attempts == 2
    assert calls["n"] == 2
    assert all("/post/" in a.url for a in result.sample_articles)

  def test_only_index_list_returned_as_ok_best_effort(
    self, monkeypatch: pytest.MonkeyPatch
  ):
    # If nothing cleaner ever appears, the count-meeting index list is still
    # returned (ok) as a best-effort starting point for the user to review.
    fake, _ = _fake_ollama([TAG_RULES])
    monkeypatch.setattr(selector_inference, "call_ollama", fake)
    result = _run_infer(html=COMBINED_HTML, max_attempts=2)
    assert result.status == "ok"
    assert len(result.sample_articles) == 3
    assert all("/tag/" in a.url for a in result.sample_articles)

  def test_count_meeting_match_beats_cleaner_too_small_one(
    self, monkeypatch: pytest.MonkeyPatch
  ):
    # A clean 1-article match scores higher, but a count-meeting (3) index list
    # is the usable result, so the final best-effort must be the count-meeting
    # one (ok), not the cleaner-but-too-small one (which would be empty).
    fake, _ = _fake_ollama([ONE_POST_RULES, TAG_RULES])
    monkeypatch.setattr(selector_inference, "call_ollama", fake)
    result = _run_infer(html=COMBINED_HTML, max_attempts=2)
    assert result.status == "ok"
    assert len(result.sample_articles) == 3
    assert all("/tag/" in a.url for a in result.sample_articles)


class TestConfigDefaults:
  def test_production_default_min_articles_is_three(self):
    # Guard the deployment default (B): the function signature keeps 2 for the
    # 2-row fixtures, but production must require 3 to reject small side-lists.
    from schemas import SelectorInferenceConfig

    cfg = SelectorInferenceConfig()
    assert cfg.min_articles == 3
    assert cfg.max_attempts == 4
