"""The background LLM pass: summarize_pending -> _summarize_rows -> _run_summarize.

The LLM is faked at summarizer.call_ollama, so the real summarize_all and
process_article decide which fields each feed mode fills. _run_summarize's own
argument wiring is checked separately with summarize_all stubbed out.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

import db
import fetcher
import repo
import summarizer
from models import Article
from schemas import AppConfig, ArticleRow, FeedRow, OllamaConfig
from tests.llm_stubs import FakeOllama

LONG: str = "x" * 400
SHORT: str = "x" * 100

_Fields = tuple[str | None, str | None, str | None]


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeOllama:
  fake_ollama = FakeOllama()
  monkeypatch.setattr(summarizer, "call_ollama", fake_ollama)
  return fake_ollama


def _feed(*, translate: bool = False, summarize: bool = False) -> FeedRow:
  return repo.add_feed(
    FeedRow(name="F", url="https://f.example.com/", translate=translate, summarize=summarize),
  )


def _store(feed: FeedRow, url: str, snippet: str, *, age_min: int = 0) -> None:
  article = Article(
    title=f"title of {url}",
    url=url,
    source=feed.name,
    # Distinct ages keep list_pending_summaries' oldest-first order stable.
    published=datetime.now(tz=UTC) - timedelta(minutes=age_min),
    content_snippet=snippet,
  )
  repo.upsert_articles([article], {feed.name: feed.id})


def _fields(url: str) -> _Fields:
  """(title_translated, summary, content_translated) as stored."""
  with db.connection() as conn:
    row = conn.execute(
      "SELECT title_translated, summary, content_translated FROM articles WHERE url = %s",
      (url,),
    ).fetchone()
  assert row is not None
  return row["title_translated"], row["summary"], row["content_translated"]


def _summarize_rows(rows: list[ArticleRow]) -> int:
  return asyncio.run(fetcher._summarize_rows(rows, AppConfig(), log_prefix="test"))


@pytest.mark.usefixtures("clean_db")
class TestSummarizePending:
  def test_disabled_ollama_does_nothing(self, fake: FakeOllama):
    _store(_feed(summarize=True), "u1", LONG)
    config = AppConfig(ollama=OllamaConfig(enabled=False))
    assert asyncio.run(fetcher.summarize_pending(config)) == 0
    assert fake.user_prompts == []
    assert _fields("u1") == (None, None, None)

  def test_nothing_pending_does_nothing(self, fake: FakeOllama):
    assert asyncio.run(fetcher.summarize_pending(AppConfig())) == 0
    assert fake.user_prompts == []

  def test_batch_size_limits_the_round_to_the_oldest_rows(self, fake: FakeOllama):
    feed = _feed(summarize=True)
    for i, url in enumerate(["old", "mid", "new"]):
      _store(feed, url, LONG, age_min=10 - i)
    assert asyncio.run(fetcher.summarize_pending(AppConfig(), batch_size=2)) == 2
    assert len(fake.user_prompts) == 2
    assert _fields("old")[1] == "S"
    assert _fields("mid")[1] == "S"
    assert _fields("new")[1] is None

  def test_counts_rows_taken_even_when_the_llm_fails(self, fake: FakeOllama):
    # Pins current behavior: the return value is the batch size, not successes.
    _store(_feed(summarize=True), "u1", LONG)
    fake.fail_titles.add("title of u1")
    assert asyncio.run(fetcher.summarize_pending(AppConfig())) == 1
    assert _fields("u1") == (None, None, None)


@pytest.mark.usefixtures("clean_db")
class TestSummarizeRows:
  @pytest.mark.parametrize(
    ("translate", "summarize", "snippet", "expected"),
    [
      (True, True, LONG, ("T", "S", None)),
      (True, False, LONG, ("T", None, None)),
      (True, True, SHORT, ("T", None, "C")),
      (True, False, SHORT, ("T", None, "C")),
      (False, True, LONG, (None, "S", None)),
    ],
    ids=[
      "translate+summarize/long",
      "translate/long",
      "translate+summarize/short",
      "translate/short",
      "summarize/long",
    ],
  )
  def test_stores_the_fields_of_each_feed_mode(
    self,
    fake: FakeOllama,
    translate: bool,
    summarize: bool,
    snippet: str,
    expected: _Fields,
  ):
    _store(_feed(translate=translate, summarize=summarize), "u1", snippet)
    assert _summarize_rows(repo.list_pending_summaries()) == 0
    assert len(fake.user_prompts) == 1
    assert _fields("u1") == expected

  def test_partial_failure_stores_only_the_successes(self, fake: FakeOllama):
    feed = _feed(summarize=True)
    _store(feed, "ok", LONG)
    _store(feed, "bad", LONG)
    fake.fail_titles.add("title of bad")
    assert _summarize_rows(repo.list_pending_summaries()) == 1
    assert _fields("ok") == (None, "S", None)
    assert _fields("bad") == (None, None, None)

  def test_empty_llm_output_keeps_the_stored_value(self, fake: FakeOllama):
    _store(_feed(translate=True, summarize=True), "u1", LONG)
    repo.update_article_summary("u1", "old title", None)
    fake.reply = {"title_translated": "", "summary": "S"}
    _summarize_rows(repo.list_pending_summaries())
    assert _fields("u1") == ("old title", "S", None)

  def test_rows_of_a_disabled_feed_are_skipped(self, fake: FakeOllama):
    feed = _feed(summarize=True)
    _store(feed, "u1", LONG)
    rows = repo.list_pending_summaries()
    with db.connection() as conn:
      conn.execute("UPDATE feeds SET enabled = FALSE WHERE id = %s", (feed.id,))
    assert _summarize_rows(rows) == 0
    assert fake.user_prompts == []

  def test_rows_without_a_feed_are_skipped(self, fake: FakeOllama):
    _store(_feed(summarize=True), "u1", LONG)
    rows = [row.model_copy(update={"feed_id": None}) for row in repo.list_pending_summaries()]
    assert _summarize_rows(rows) == 0
    assert fake.user_prompts == []


class TestRunSummarize:
  @pytest.fixture
  def calls(self, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    recorded: list[dict[str, Any]] = []

    async def fake_summarize_all(articles: list[Article], **kwargs: Any) -> int:
      recorded.append({"articles": articles, **kwargs})
      return 2

    monkeypatch.setattr(fetcher, "summarize_all", fake_summarize_all)
    return recorded

  @staticmethod
  def _run(articles: list[Article], config: AppConfig | None = None) -> int:
    return asyncio.run(
      fetcher._run_summarize(articles, {"src"}, {"src"}, config or AppConfig()),
    )

  def test_short_set_holds_non_empty_bodies_under_300_chars(self, calls: list[dict[str, Any]]):
    articles = [
      Article(title="t", url=url, source="src", published=None, content_snippet=snippet)
      for url, snippet in [("299", "x" * 299), ("300", "x" * 300), ("empty", "")]
    ]
    self._run(articles)
    assert calls[0]["short_set"] == {"299"}

  def test_passes_config_and_returns_the_failure_count(
    self,
    calls: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
  ):
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    config = AppConfig(
      native_lang="ja",
      ollama=OllamaConfig(model="m", base_url="http://cfg:11434", timeout=7),
    )
    assert self._run([], config) == 2
    call = calls[0]
    assert (call["model"], call["base_url"], call["timeout"], call["native_lang"]) == (
      "m",
      "http://cfg:11434",
      7,
      "ja",
    )
    assert (call["translate_set"], call["summarize_set"]) == ({"src"}, {"src"})

  def test_env_base_url_overrides_config(
    self,
    calls: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
  ):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://env:11434")
    self._run([], AppConfig(ollama=OllamaConfig(base_url="http://cfg:11434")))
    assert calls[0]["base_url"] == "http://env:11434"
