"""Tests for fetcher._needs_llm.

This function must stay in lockstep with repo.list_pending_summaries;
together they gate which articles get LLM processing. The done-condition
depends on (translate, summarize, short): translate-only long articles are
complete once the title is translated, which prevents them from bouncing
back into the queue forever.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fetcher import SHORT_SNIPPET_CHARS, _needs_llm
from models import Article

LONG = "x" * SHORT_SNIPPET_CHARS
SHORT = "x" * (SHORT_SNIPPET_CHARS - 1)


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


class TestTranslateAndSummarize:
  def test_missing_title_translation_needs_llm(self):
    assert _needs_llm(
      _art(summary="要約", content_snippet=LONG), translate=True, summarize=True
    )

  def test_long_title_without_summary_needs_llm(self):
    assert _needs_llm(
      _art(title_translated="訳", content_snippet=LONG),
      translate=True,
      summarize=True,
    )

  def test_long_title_and_summary_satisfies(self):
    assert not _needs_llm(
      _art(title_translated="訳", summary="要約", content_snippet=LONG),
      translate=True,
      summarize=True,
    )

  def test_short_title_and_content_translated_satisfies(self):
    assert not _needs_llm(
      _art(title_translated="訳", content_translated="翻訳本文", content_snippet=SHORT),
      translate=True,
      summarize=True,
    )

  def test_short_title_without_content_translated_needs_llm(self):
    assert _needs_llm(
      _art(title_translated="訳", content_snippet=SHORT),
      translate=True,
      summarize=True,
    )


class TestTranslateOnly:
  def test_missing_title_needs_llm(self):
    assert _needs_llm(_art(content_snippet=LONG), translate=True, summarize=False)

  def test_long_title_alone_satisfies(self):
    # Key anti-loop guarantee: a translated title is enough for a long
    # translate-only article (no summary / content_translated expected).
    assert not _needs_llm(
      _art(title_translated="訳", content_snippet=LONG),
      translate=True,
      summarize=False,
    )

  def test_short_still_requires_full_translation(self):
    assert _needs_llm(
      _art(title_translated="訳", content_snippet=SHORT),
      translate=True,
      summarize=False,
    )
    assert not _needs_llm(
      _art(title_translated="訳", content_translated="翻訳本文", content_snippet=SHORT),
      translate=True,
      summarize=False,
    )


class TestEmptyBodyTranslate:
  # An empty body cannot fill content_translated/summary, so the title
  # translation alone must complete the article (anti-loop), regardless of
  # the summarize flag.
  def test_empty_body_missing_title_needs_llm(self):
    assert _needs_llm(_art(content_snippet=""), translate=True, summarize=True)
    assert _needs_llm(_art(content_snippet=""), translate=True, summarize=False)

  def test_empty_body_with_title_satisfies(self):
    assert not _needs_llm(
      _art(title_translated="訳", content_snippet=""),
      translate=True,
      summarize=True,
    )
    assert not _needs_llm(
      _art(title_translated="訳", content_snippet=""),
      translate=True,
      summarize=False,
    )


class TestSummarizeOnly:
  def test_summary_present_no_llm(self):
    assert not _needs_llm(
      _art(summary="done", content_snippet=LONG), translate=False, summarize=True
    )

  def test_short_content_no_llm(self):
    assert not _needs_llm(
      _art(content_snippet=SHORT), translate=False, summarize=True
    )

  def test_long_content_without_summary_needs_llm(self):
    assert _needs_llm(
      _art(content_snippet=LONG), translate=False, summarize=True
    )

  def test_empty_content_not_needed(self):
    # Empty snippet is treated as short, mirroring the SQL pending query which
    # excludes native feeds whose content is shorter than the threshold.
    assert not _needs_llm(
      _art(content_snippet=""), translate=False, summarize=True
    )


class TestNeitherFlag:
  def test_no_llm_regardless_of_state(self):
    assert not _needs_llm(_art(content_snippet=LONG), translate=False, summarize=False)
    assert not _needs_llm(_art(content_snippet=SHORT), translate=False, summarize=False)
