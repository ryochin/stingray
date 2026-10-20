"""Tests for summarizer: prompt selection per mode and summarize_all routing.

call_ollama is monkeypatched with an async fake that records the system prompt
it receives, so these run without a live LLM or database.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

import summarizer
from models import Article
from tests.llm_stubs import FakeOllama

# Distinctive fragments of each system prompt, used to tell which one was sent.
FULL = "full article content translated"
TITLE_ONLY = "Do NOT translate or summarize the body"
TRANSLATE_SUMMARY = '"title_translated": The article title translated into Japanese.\n- "summary"'
SUMMARY_ONLY = 'Do NOT include a "title_translated" field'


def _article(
  url: str = "https://example.com/a",
  source: str = "src",
  title: str = "Original",
) -> Article:
  return Article(title=title, url=url, source=source, published=None, content_snippet="body")


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeOllama:
  fake_ollama = FakeOllama()
  monkeypatch.setattr(summarizer, "call_ollama", fake_ollama)
  return fake_ollama


def _process(article: Article, **kwargs: Any) -> tuple[str, str, str]:
  async def run() -> tuple[str, str, str]:
    async with httpx.AsyncClient() as client:
      return await summarizer.process_article(client, article, "m", native_lang="ja", **kwargs)

  return asyncio.run(run())


# --- process_article: mode selection ---


@pytest.mark.parametrize("summarize", [True, False])
def test_translate_short_uses_full_translation(fake: FakeOllama, summarize: bool) -> None:
  result = _process(_article(), translate=True, summarize=summarize, short=True)
  assert result == ("T", "", "C")
  assert FULL in fake.prompts[0]


def test_translate_long_without_summary_translates_title_only(fake: FakeOllama) -> None:
  result = _process(_article(), translate=True, summarize=False)
  assert result == ("T", "", "")
  assert TITLE_ONLY in fake.prompts[0]


def test_translate_long_with_summary_translates_title_and_summarizes(fake: FakeOllama) -> None:
  result = _process(_article(), translate=True, summarize=True)
  assert result == ("T", "S", "")
  assert TRANSLATE_SUMMARY in fake.prompts[0]


@pytest.mark.parametrize("short", [True, False])
def test_native_language_summarizes_only(fake: FakeOllama, short: bool) -> None:
  result = _process(_article(), translate=False, summarize=True, short=short)
  assert result == ("", "S", "")
  assert SUMMARY_ONLY in fake.prompts[0]


def test_user_prompt_contains_title_and_snippet(fake: FakeOllama) -> None:
  _process(_article(title="Hello"), translate=False, summarize=True)
  assert fake.user_prompts == ["Title: Hello\n\nbody"]


@pytest.mark.parametrize(
  ("native_lang", "expected"),
  [("ja", "into Japanese"), ("en", "in English"), ("fr", "in fr")],
)
def test_prompt_uses_native_language_display_name(
  fake: FakeOllama,
  native_lang: str,
  expected: str,
) -> None:
  async def run() -> None:
    async with httpx.AsyncClient() as client:
      await summarizer.process_article(
        client,
        _article(),
        "m",
        translate=True,
        summarize=True,
        native_lang=native_lang,
      )

  asyncio.run(run())
  assert expected in fake.prompts[0]


# --- process_article: fallbacks for missing or non-str fields ---


@pytest.mark.parametrize(
  "reply", [{}, {"title_translated": 1, "summary": ["x"], "content_translated": None}]
)
@pytest.mark.parametrize(
  ("kwargs", "expected"),
  [
    ({"translate": True, "summarize": False, "short": True}, ("Original", "", "")),
    ({"translate": True, "summarize": False}, ("Original", "", "")),
    ({"translate": True, "summarize": True}, ("Original", "", "")),
    ({"translate": False, "summarize": True}, ("", "", "")),
  ],
)
def test_bad_llm_fields_fall_back(
  fake: FakeOllama,
  reply: dict[str, Any],
  kwargs: dict[str, bool],
  expected: tuple[str, str, str],
) -> None:
  fake.reply = reply
  assert _process(_article(), **kwargs) == expected


# --- summarize_all ---


def _summarize_all(articles: list[Article], **kwargs: Any) -> int:
  return asyncio.run(summarizer.summarize_all(articles, native_lang="ja", **kwargs))


def test_summarize_all_routes_by_source_and_url(fake: FakeOllama) -> None:
  short = _article(url="https://x/short", source="foreign", title="Short")
  long_ = _article(url="https://x/long", source="foreign", title="Long")
  title_only = _article(url="https://x/t", source="foreign-nosum", title="TitleOnly")
  native = _article(url="https://x/n", source="native", title="Native")

  failures = _summarize_all(
    [short, long_, title_only, native],
    translate_set={"foreign", "foreign-nosum"},
    summarize_set={"foreign", "native"},
    short_set={"https://x/short"},
  )

  assert failures == 0
  by_title = dict(zip([p.split("\n")[0] for p in fake.user_prompts], fake.prompts, strict=True))
  assert FULL in by_title["Title: Short"]
  assert TRANSLATE_SUMMARY in by_title["Title: Long"]
  assert TITLE_ONLY in by_title["Title: TitleOnly"]
  assert SUMMARY_ONLY in by_title["Title: Native"]
  assert (short.title_translated, short.summary, short.content_translated) == ("T", "", "C")
  assert (long_.title_translated, long_.summary, long_.content_translated) == ("T", "S", "")
  assert (native.title_translated, native.summary, native.content_translated) == ("", "S", "")


@pytest.mark.parametrize("empty", [None, set[str]()])
def test_summarize_all_none_and_empty_sets_match(fake: FakeOllama, empty: set[str] | None) -> None:
  article = _article()
  failures = _summarize_all([article], translate_set=empty, summarize_set=empty, short_set=empty)
  assert failures == 0
  assert SUMMARY_ONLY in fake.prompts[0]
  assert (article.title_translated, article.summary, article.content_translated) == ("", "S", "")


def test_summarize_all_empty_list(fake: FakeOllama) -> None:
  assert _summarize_all([]) == 0
  assert fake.prompts == []


def test_summarize_all_counts_failures_and_leaves_failed_articles_untouched(
  fake: FakeOllama,
) -> None:
  ok = _article(url="https://x/ok", title="Ok")
  bad = _article(url="https://x/bad", title="Bad")
  bad.title_translated = "old title"
  bad.summary = "old summary"
  bad.content_translated = "old content"
  fake.fail_titles = {"Bad"}

  failures = _summarize_all([ok, bad], translate_set={"src"}, summarize_set={"src"})

  assert failures == 1
  assert (ok.title_translated, ok.summary) == ("T", "S")
  assert (bad.title_translated, bad.summary, bad.content_translated) == (
    "old title",
    "old summary",
    "old content",
  )
