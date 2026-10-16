"""The fetcher's refresh LLM pass and the web's background summarizer share
one cross-process advisory lock, so they never feed the same rows to Ollama.

The "other process" is a second pooled connection holding the lock; session
advisory locks only see sessions, so that is indistinguishable from another
process. The LLM is replaced by a stub that records what it was given.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import db
import fetcher
import repo
from models import Article
from schemas import AppConfig, FeedRow

pytestmark = pytest.mark.usefixtures("clean_db")

LONG_A = "a" * 400
LONG_B = "b" * 400


@pytest.fixture
def llm_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
  """Stub the LLM: summarize each article and record (url, snippet)."""
  calls: list[tuple[str, str]] = []

  async def fake_run_summarize(
    need_llm: list[Article], *_args: Any, **_kwargs: Any,
  ) -> int:
    for article in need_llm:
      calls.append((article.url, article.content_snippet))
      article.summary = f"summary of {article.url}"
    return 0

  monkeypatch.setattr(fetcher, "_run_summarize", fake_run_summarize)
  monkeypatch.setattr(fetcher, "SUMMARIZE_LOCK_POLL_SECONDS", 0.01)
  return calls


def _feed() -> FeedRow:
  return repo.add_feed(FeedRow(name="F", url="https://f.example.com/", summarize=True))


def _store(feed: FeedRow, url: str, snippet: str) -> Article:
  article = Article(
    title="T", url=url, source=feed.name,
    published=datetime.now(tz=UTC), content_snippet=snippet,
  )
  repo.upsert_articles([article], {feed.name: feed.id})
  return article


def _summary(url: str) -> str | None:
  with db.connection() as conn:
    row = conn.execute("SELECT summary FROM articles WHERE url = %s", (url,)).fetchone()
  assert row is not None
  return row["summary"]


class TestBackgroundSummarizer:
  def test_skips_the_round_while_the_lock_is_held_elsewhere(self, llm_calls):
    _store(_feed(), "u1", LONG_A)
    with repo.advisory_lock(repo.SUMMARIZE_LOCK_KEY) as got:
      assert got is True
      assert asyncio.run(fetcher.summarize_pending(AppConfig())) == 0
    assert llm_calls == []
    assert _summary("u1") is None

  def test_processes_and_stores_a_batch(self, llm_calls):
    _store(_feed(), "u1", LONG_A)
    assert asyncio.run(fetcher.summarize_pending(AppConfig())) == 1
    assert llm_calls == [("u1", LONG_A)]
    assert _summary("u1") == "summary of u1"


class TestRefreshLlmPass:
  def test_waits_for_the_lock_then_rereads_pending_rows(self, llm_calls, monkeypatch):
    feed = _feed()
    done_meanwhile = _store(feed, "u1", LONG_A)
    stale = _store(feed, "u2", LONG_A)
    real_advisory_lock = repo.advisory_lock

    async def scenario() -> int:
      refused = asyncio.Event()

      @contextmanager
      def spying_lock(key: int = repo.REFRESH_LOCK_KEY) -> Iterator[bool]:
        with real_advisory_lock(key) as got:
          if not got:
            refused.set()
          yield got

      with real_advisory_lock(repo.SUMMARIZE_LOCK_KEY) as got:
        assert got is True
        monkeypatch.setattr(repo, "advisory_lock", spying_lock)
        task = asyncio.create_task(
          fetcher._run_llm_pass([done_meanwhile, stale], [feed], AppConfig()),
        )
        # Proof the pass actually tried the lock and was turned away.
        await asyncio.wait_for(refused.wait(), timeout=5)
        assert llm_calls == []
        # While the pass waits, the lock holder finishes u1 and another feed
        # carrying u2 rewrites its body.
        repo.update_article_summary("u1", None, "by the other process")
        with db.connection() as conn:
          conn.execute(
            "UPDATE articles SET content_snippet = %s WHERE url = 'u2'", (LONG_B,),
          )
      return await task

    assert asyncio.run(scenario()) == 0
    assert llm_calls == [("u2", LONG_B)]
    assert _summary("u1") == "by the other process"
    assert _summary("u2") == "summary of u2"

  def test_releases_the_lock_afterwards(self, llm_calls):
    feed = _feed()
    article = _store(feed, "u1", LONG_A)
    asyncio.run(fetcher._run_llm_pass([article], [feed], AppConfig()))
    assert llm_calls == [("u1", LONG_A)]
    # pg_locks, not a re-acquire: a pooled connection that kept the lock
    # would grant it again to itself.
    with db.connection() as conn:
      row = conn.execute(
        """SELECT COUNT(*) AS n FROM pg_locks
           WHERE locktype = 'advisory' AND objsubid = 1
             AND classid = %s AND objid = %s""",
        (repo.SUMMARIZE_LOCK_KEY >> 32, repo.SUMMARIZE_LOCK_KEY & 0xFFFF_FFFF),
      ).fetchone()
    assert row is not None and row["n"] == 0

  def test_leaves_pending_rows_outside_this_refresh_to_the_background(self, llm_calls):
    """A backlog (say, a feed that just turned translation on) would otherwise
    be pulled into one refresh all at once."""
    feed = _feed()
    fetched = _store(feed, "u1", LONG_A)
    _store(feed, "backlog", LONG_A)
    asyncio.run(fetcher._run_llm_pass([fetched], [feed], AppConfig()))
    assert llm_calls == [("u1", LONG_A)]
    assert _summary("backlog") is None


class TestLegacyCacheRestore:
  def test_restored_fields_are_stored_before_the_llm_pass_rereads(
    self, llm_calls, monkeypatch, tmp_path: Path,
  ):
    """The pass rebuilds articles from DB rows, so values restored only in
    memory would be lost (the legacy file is renamed after one read)."""
    feed = repo.add_feed(
      FeedRow(name="F", url="https://f.example.com/", translate=True, summarize=True),
    )
    article = _store(feed, "u1", LONG_A)
    (tmp_path / "articles.json").write_text(
      '{"u1": {"title_ja": "restored title"}}', encoding="utf-8",
    )

    async def fake_fetch(*_args: Any, **_kwargs: Any) -> tuple[list[Article], int]:
      return [article], 0

    monkeypatch.setattr(fetcher, "_fetch_and_persist_feeds", fake_fetch)
    monkeypatch.setattr(fetcher, "_backfill_site_urls", lambda *_a, **_k: asyncio.sleep(0))
    config = AppConfig(cache_dir=str(tmp_path))

    asyncio.run(fetcher.refresh_all(config, source="test", force=True))

    with db.connection() as conn:
      row = conn.execute(
        "SELECT title_translated, summary FROM articles WHERE url = 'u1'",
      ).fetchone()
    assert row == {"title_translated": "restored title", "summary": "summary of u1"}
    assert (tmp_path / "articles.json.migrated").exists()
