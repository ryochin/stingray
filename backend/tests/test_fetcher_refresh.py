"""`fetcher.refresh_all` and `_fetch_and_persist_feeds` against the test DB.

`_fetch_and_persist_feeds` runs the real fetch_all over a MockTransport, so a
fetch result is followed all the way to the article rows and the schedule
columns `record_feed_attempt` writes. `refresh_all` tests stub the fetch, LLM
and site-url stages and look at the refresh_jobs row and which stages ran.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

import db
import fetcher
import repo
from models import Article
from schemas import AppConfig, FeedRow
from tests.fetch_stubs import by_host, rss, stub_network

pytestmark = pytest.mark.usefixtures("clean_db")

NOW = datetime.now(tz=UTC)


def _add_feed(name: str, **fields: Any) -> FeedRow:
  return repo.add_feed(FeedRow(name=name, url=f"https://{name}.example.com/feed", **fields))


def _feed_state(feed_id: int) -> dict[str, Any]:
  with db.connection() as conn:
    row = conn.execute(
      "SELECT health, consecutive_failures, last_error, last_fetched_at FROM feeds WHERE id = %s",
      (feed_id,),
    ).fetchone()
  assert row is not None
  return dict(row)


def _article_urls() -> list[str]:
  with db.connection() as conn:
    rows = conn.execute("SELECT url FROM articles ORDER BY url").fetchall()
  return [r["url"] for r in rows]


def _job(job_id: int) -> dict[str, Any]:
  with db.connection() as conn:
    row = conn.execute(
      "SELECT status, new_count, error FROM refresh_jobs WHERE id = %s",
      (job_id,),
    ).fetchone()
  assert row is not None
  return dict(row)


def _persist(feeds: list[FeedRow]) -> tuple[list[Article], int]:
  return asyncio.run(
    fetcher._fetch_and_persist_feeds(
      [f.to_feed_cfg() for f in feeds],
      fetcher._build_feed_id_map(feeds),
      AppConfig(),
    )
  )


class TestFetchAndPersist:
  def test_success_stores_articles_and_clears_failure_state(
    self,
    monkeypatch: pytest.MonkeyPatch,
  ) -> None:
    feed = _add_feed("ok")
    # Start from a failing feed so a missing recovery would show.
    with db.connection() as conn:
      conn.execute(
        "UPDATE feeds SET health = 'failing', consecutive_failures = 2, "
        "last_error = 'old' WHERE id = %s",
        (feed.id,),
      )
    body = rss(("https://ok.example.com/1", NOW), ("https://ok.example.com/2", NOW))
    stub_network(monkeypatch, lambda _req: httpx.Response(200, text=body))

    articles, new_count = _persist([feed])

    assert len(articles) == 2
    assert new_count == 2
    assert _article_urls() == ["https://ok.example.com/1", "https://ok.example.com/2"]
    state = _feed_state(feed.id)
    assert state["health"] == "ok"
    assert state["consecutive_failures"] == 0
    assert state["last_error"] is None
    assert state["last_fetched_at"] is not None

  def test_http_error_marks_only_that_feed_failing(self, monkeypatch: pytest.MonkeyPatch) -> None:
    ok = _add_feed("ok")
    gone = _add_feed("gone")
    stub_network(
      monkeypatch,
      by_host(
        {
          "ok": httpx.Response(200, text=rss(("https://ok.example.com/1", NOW))),
          "gone": httpx.Response(404),
        }
      ),
    )

    _, new_count = _persist([ok, gone])

    assert new_count == 1
    assert _article_urls() == ["https://ok.example.com/1"]
    assert _feed_state(ok.id)["health"] == "ok"
    state = _feed_state(gone.id)
    assert state["health"] == "failing"
    assert state["consecutive_failures"] == 1
    assert "404" in state["last_error"]

  def test_upsert_error_marks_only_that_feed_failing(self, monkeypatch: pytest.MonkeyPatch) -> None:
    good = _add_feed("good")
    bad = _add_feed("bad")
    stub_network(
      monkeypatch,
      lambda req: httpx.Response(200, text=rss((f"https://{req.url.host}/1", NOW))),
    )
    real_upsert = repo.upsert_articles

    def flaky_upsert(articles: list[Article], feed_id_map: dict[str, int]) -> int:
      if articles[0].source == "bad":
        raise RuntimeError("disk full")
      return real_upsert(articles, feed_id_map)

    monkeypatch.setattr(repo, "upsert_articles", flaky_upsert)

    _, new_count = _persist([good, bad])

    assert new_count == 1
    assert _article_urls() == ["https://good.example.com/1"]
    assert _feed_state(good.id)["health"] == "ok"
    state = _feed_state(bad.id)
    assert state["health"] == "failing"
    assert state["last_error"] == "RuntimeError: disk full"

  def test_new_count_sums_every_feed(self, monkeypatch: pytest.MonkeyPatch) -> None:
    feeds = [_add_feed(f"f{i}") for i in range(3)]
    stub_network(
      monkeypatch,
      lambda req: httpx.Response(
        200,
        text=rss((f"https://{req.url.host}/1", NOW), (f"https://{req.url.host}/2", NOW)),
      ),
    )

    _, new_count = _persist(feeds)

    assert new_count == 6

  def test_schedule_write_error_is_logged_and_swallowed(
    self,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
  ) -> None:
    feed = _add_feed("ok")
    stub_network(
      monkeypatch,
      lambda _req: httpx.Response(200, text=rss(("https://ok.example.com/1", NOW))),
    )

    def broken_record(*_args: Any, **_kwargs: Any) -> None:
      raise RuntimeError("lock timeout")

    monkeypatch.setattr(repo, "record_feed_attempt", broken_record)

    articles, new_count = _persist([feed])

    assert [a.url for a in articles] == ["https://ok.example.com/1"]
    assert new_count == 1
    # fetch_all would also swallow it as [persist-error]; the schedule
    # write must be caught by its own handler.
    assert "[schedule-error] ok: RuntimeError: lock timeout" in capsys.readouterr().out

  def test_stale_cache_is_degraded_then_an_unchanged_body_recovers(
    self,
    monkeypatch: pytest.MonkeyPatch,
  ) -> None:
    feed = _add_feed("f")
    body = rss(("https://f.example.com/1", NOW))
    responses = [
      httpx.Response(200, text=body),
      httpx.Response(503),
      httpx.Response(200, text=body),
    ]
    stub_network(monkeypatch, lambda _req: responses.pop(0))

    _persist([feed])
    _persist([feed])
    degraded = _feed_state(feed.id)
    _, new_count = _persist([feed])

    assert degraded["health"] == "degraded"
    assert degraded["last_error"] is not None
    assert new_count == 0
    assert _feed_state(feed.id)["health"] == "ok"
    # A miss widens the interval (10 -> 30); a fresh outcome would keep it at 10.
    with db.connection() as conn:
      row = conn.execute(
        "SELECT fetch_interval_min FROM feeds WHERE id = %s",
        (feed.id,),
      ).fetchone()
    assert row == {"fetch_interval_min": 30}


class _Stages:
  """Records which refresh_all stages ran; the fetch returns `articles`."""

  def __init__(self, monkeypatch: pytest.MonkeyPatch, articles: list[Article]) -> None:
    self.fetched: list[list[str]] = []
    self.llm = 0
    self.backfill = 0

    async def fake_fetch(
      feeds_cfg: list[dict[str, Any]],
      *_args: Any,
      **_kwargs: Any,
    ) -> tuple[list[Article], int]:
      self.fetched.append([cfg["name"] for cfg in feeds_cfg])
      return articles, len(articles)

    async def fake_llm(*_args: Any, **_kwargs: Any) -> int:
      self.llm += 1
      return 0

    async def fake_backfill(*_args: Any, **_kwargs: Any) -> None:
      self.backfill += 1

    monkeypatch.setattr(fetcher, "_fetch_and_persist_feeds", fake_fetch)
    monkeypatch.setattr(fetcher, "_run_llm_pass", fake_llm)
    monkeypatch.setattr(fetcher, "_backfill_site_urls", fake_backfill)


def _some_article() -> Article:
  return Article(title="T", url="u1", source="F", published=NOW, content_snippet="x")


def _refresh(config: AppConfig, **kwargs: Any) -> fetcher.RefreshResult:
  return asyncio.run(fetcher.refresh_all(config, source="test", **kwargs))


@pytest.fixture
def config(tmp_path: Path) -> AppConfig:
  # enrich_from_legacy_cache renames files under cache_dir; keep it in tmp.
  return AppConfig(cache_dir=str(tmp_path))


def _latest_job() -> dict[str, Any]:
  with db.connection() as conn:
    row = conn.execute("SELECT id FROM refresh_jobs ORDER BY id DESC LIMIT 1").fetchone()
  assert row is not None
  return _job(row["id"])


class TestRefreshAll:
  def test_failure_closes_the_job_as_failed_and_reraises(
    self,
    monkeypatch: pytest.MonkeyPatch,
    config: AppConfig,
  ) -> None:
    _add_feed("F")
    _Stages(monkeypatch, [_some_article()])

    def broken_prune(_days: int) -> int:
      raise RuntimeError("prune exploded")

    monkeypatch.setattr(repo, "prune_articles", broken_prune)

    with pytest.raises(RuntimeError, match="prune exploded"):
      _refresh(config, force=True)

    job = _latest_job()
    assert job["status"] == "failed"
    assert job["error"] == "prune exploded"

  def test_skips_while_another_refresh_holds_the_lock(
    self,
    monkeypatch: pytest.MonkeyPatch,
    config: AppConfig,
  ) -> None:
    _add_feed("F")
    stages = _Stages(monkeypatch, [_some_article()])

    with repo.advisory_lock() as got:
      assert got is True
      _refresh(config, force=True)

    assert _latest_job()["status"] == "skipped"
    assert stages.fetched == []

  def test_only_due_feeds_unless_forced(
    self,
    monkeypatch: pytest.MonkeyPatch,
    config: AppConfig,
  ) -> None:
    _add_feed("never")
    past = _add_feed("past")
    future = _add_feed("future")
    _add_feed("disabled", enabled=False)
    # add_feed does not write next_fetch_at.
    with db.connection() as conn:
      for feed, at in ((past, NOW - timedelta(minutes=1)), (future, NOW + timedelta(hours=1))):
        conn.execute("UPDATE feeds SET next_fetch_at = %s WHERE id = %s", (at, feed.id))
    stages = _Stages(monkeypatch, [])

    _refresh(config)
    _refresh(config, force=True)

    assert sorted(stages.fetched[0]) == ["never", "past"]
    assert sorted(stages.fetched[1]) == ["future", "never", "past"]

  @pytest.mark.parametrize("has_feed", [False, True])
  def test_nothing_to_do_completes_without_later_stages(
    self,
    monkeypatch: pytest.MonkeyPatch,
    config: AppConfig,
    has_feed: bool,
  ) -> None:
    # No due feed at all, or a feed that yields no articles.
    if has_feed:
      _add_feed("F")
    stages = _Stages(monkeypatch, [])

    result = _refresh(config, force=True)

    assert result.new_count == 0
    assert _latest_job() == {"status": "completed", "new_count": 0, "error": None}
    assert stages.fetched == ([["F"]] if has_feed else [])
    assert stages.llm == 0
    assert stages.backfill == 0

  @pytest.mark.parametrize(
    ("no_summary", "ollama_enabled", "llm_runs"),
    [(False, True, 1), (True, True, 0), (False, False, 0)],
  )
  def test_llm_pass_respects_no_summary_and_ollama_switch(
    self,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    no_summary: bool,
    ollama_enabled: bool,
    llm_runs: int,
  ) -> None:
    _add_feed("F")
    stages = _Stages(monkeypatch, [_some_article()])
    config = AppConfig.model_validate(
      {"cache_dir": str(tmp_path), "ollama": {"enabled": ollama_enabled}},
    )

    result = _refresh(config, force=True, no_summary=no_summary)

    assert stages.llm == llm_runs
    assert stages.backfill == 1
    assert result.new_count == 1
    assert _latest_job() == {"status": "completed", "new_count": 1, "error": None}

  def test_uses_the_given_job_id(self, monkeypatch: pytest.MonkeyPatch, config: AppConfig) -> None:
    _Stages(monkeypatch, [])
    job_id = repo.create_refresh_job("web")

    _refresh(config, job_id=job_id, force=True)

    with db.connection() as conn:
      count = conn.execute("SELECT COUNT(*) AS n FROM refresh_jobs").fetchone()
    assert count is not None
    assert count["n"] == 1
    assert _job(job_id)["status"] == "completed"
