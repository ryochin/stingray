"""Tests for fetcher._needs_llm.

This function must stay in lockstep with repo.list_pending_summaries;
together they gate which articles get LLM processing. When translate
is on, non-translated items need LLM. When translate is off, only
sufficiently long items benefit from summarization.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fetcher import SHORT_SNIPPET_CHARS, _needs_llm
from models import Article


def _art(
  *,
  title_translated: str = "",
  summary: str = "",
  content_translated: str = "",
  content_snippet: str = "",
) -> Article:
  return Article(
    title="t",
    url="u",
    source="s",
    published=datetime.now(tz=timezone.utc),
    content_snippet=content_snippet,
    title_translated=title_translated,
    summary=summary,
    content_translated=content_translated,
  )


class TestTranslateOn:
  def test_missing_title_translation_needs_llm(self):
    assert _needs_llm(_art(summary="要約"), translate=True)

  def test_missing_both_summary_and_content_needs_llm(self):
    assert _needs_llm(_art(title_translated="訳"), translate=True)

  def test_title_and_summary_satisfies(self):
    assert not _needs_llm(_art(title_translated="訳", summary="要約"), translate=True)

  def test_title_and_content_translated_satisfies(self):
    # Short-content path: content_translated alone is enough (no summary required).
    assert not _needs_llm(
      _art(title_translated="訳", content_translated="翻訳本文"),
      translate=True,
    )

  def test_all_missing_needs_llm(self):
    assert _needs_llm(_art(), translate=True)


class TestTranslateOff:
  def test_summary_present_no_llm(self):
    assert not _needs_llm(_art(summary="done"), translate=False)

  def test_short_content_no_llm(self):
    short = "x" * (SHORT_SNIPPET_CHARS - 1)
    assert not _needs_llm(_art(content_snippet=short), translate=False)

  def test_long_content_without_summary_needs_llm(self):
    long = "x" * SHORT_SNIPPET_CHARS
    assert _needs_llm(_art(content_snippet=long), translate=False)

  def test_empty_content_treated_as_needing_llm(self):
    # Current behavior: an empty content_snippet falls through to the "needs LLM" path
    # because `len("") < 300` is True only when content_snippet is truthy. Empty
    # string is falsy, so the early-return is skipped. Documents the edge case —
    # the feed-level summarize flag is what ultimately gates processing.
    assert _needs_llm(_art(content_snippet=""), translate=False)
