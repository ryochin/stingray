"""Unified fetch + summarize + persist entry point for CLI and Web."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import log
import repo
from feeds import extract_site_url, fetch_all
from models import Article
from safe_http import FetchSettings, SafeClient
from schemas import AppConfig, ArticleRow, FeedRow
from seed import enrich_from_legacy_cache
from summarizer import SummarizeOutcome, summarize_all
from url_cleaner import clean_url

SHORT_SNIPPET_CHARS = 300
SUMMARIZE_LOCK_POLL_SECONDS = 5.0


@dataclass
class RefreshResult:
  total_articles: int = 0
  new_count: int = 0
  summarize_failures: int = 0


def _needs_llm(article: Article, *, translate: bool, summarize: bool) -> bool:
  """Check if an article still needs LLM processing.

  The done-condition mirrors `process_article`'s output per
  (translate, summarize, short) so already-processed articles never bounce
  back into the queue (see repo.list_pending_summaries).
  """
  snippet = article.content_snippet or ""
  if translate:
    if not article.title_translated:
      return True
    if len(snippet) == 0:
      # No body to translate or summarize → the title alone completes it.
      # (An empty body can never fill content_translated/summary, so requiring
      # either would re-queue the article forever.)
      return False
    if len(snippet) < SHORT_SNIPPET_CHARS:
      # Short foreign article → full-body translation required.
      return not article.content_translated
    if summarize:
      # Long foreign article with summary → translated summary required.
      return not article.summary
    # Long foreign article, summary disabled → title translation is enough.
    return False
  if not summarize:
    return False
  # Native summarize: only content long enough to summarize (>= threshold).
  if len(snippet) < SHORT_SNIPPET_CHARS:
    return False
  return not article.summary


# Source tags that mean "we served a stale cached copy" mapped to the
# diagnostic recorded on the feed.
STALE_CACHE_DIAGNOSTICS: dict[str, str] = {
  "5xx-cache": "serving stale cache: origin returned HTTP 4xx/5xx (blocked?)",
  "net-cache": "serving stale cache: network error reaching origin",
  "304-empty": "304 Not Modified but no cached copy available",
}


def classify_outcome(
  source_tag: str | None,
  feed_kind: str,
  fetch_exc: Exception | None,
  persist_exc: Exception | None,
  inserted_count: int,
) -> tuple[repo.Outcome, str | None]:
  """Fold the 5 per-feed observations into a scheduler outcome + error string.

  Shared by the scheduled path and the manual single-feed fetch
  (app._fetch_single_feed), so both classify a feed's health identically.

  Returns (outcome, error) where outcome is one of
  "fresh" / "miss" / "degraded" / "failure".
  """
  if fetch_exc is not None:
    return "failure", f"{type(fetch_exc).__name__}: {fetch_exc}"
  if persist_exc is not None:
    return "failure", f"{type(persist_exc).__name__}: {persist_exc}"
  if feed_kind == "web-norules":
    return "degraded", "extraction rules not configured"
  # Serving a stale cached copy: record why so the feed shows up as degraded
  # with a diagnostic instead of silently looking healthy on old content.
  stale_diagnostic = STALE_CACHE_DIAGNOSTICS.get(source_tag or "")
  if stale_diagnostic is not None:
    return "degraded", stale_diagnostic
  # Normal path: fresh body or clean 304 / unchanged / web success (None tag).
  return ("fresh" if inserted_count >= 1 else "miss"), None


async def _fetch_and_persist_feeds(
  feeds_cfg: list[dict[str, Any]],
  config: AppConfig,
  *,
  update_schedule: bool = True,
) -> tuple[list[Article], int]:
  """Fetch all feeds in parallel and upsert each feed's articles as soon as it
  finishes. The per-feed callback also classifies the fetch outcome and, when
  `update_schedule` is True, records it via `repo.record_feed_attempt` so the
  adaptive interval learns from this cycle.
  """
  new_count_total = 0
  new_count_lock = asyncio.Lock()

  async def _persist_feed(
    feed_cfg: dict[str, Any],
    feed_articles: list[Article],
    source_tag: str | None,
    feed_kind: str,
    fetch_exc: Exception | None,
  ) -> None:
    nonlocal new_count_total
    # Only upsert is wrapped — LLM summarization runs separately after all
    # fetches and must not contribute to the schedule signal.
    inserted_count = 0
    persist_exc: Exception | None = None
    if feed_articles:
      try:
        inserted_count = await asyncio.to_thread(
          repo.upsert_articles,
          feed_articles,
        )
      except Exception as e:
        persist_exc = e
    if inserted_count:
      async with new_count_lock:
        new_count_total += inserted_count
      name = feed_cfg.get("name") or "?"
      log.info(f"  [db] {name}: +{inserted_count} new")

    if not update_schedule:
      return
    feed_id = feed_cfg.get("id")
    if feed_id is None:
      return
    outcome, error = classify_outcome(
      source_tag,
      feed_kind,
      fetch_exc,
      persist_exc,
      inserted_count,
    )
    try:
      await asyncio.to_thread(
        repo.record_feed_attempt,
        feed_id,
        outcome,
        error=error,
      )
    except Exception as e:
      log.error(f"  [schedule-error] {feed_cfg.get('name')}: {type(e).__name__}: {e}")

  articles = await fetch_all(
    feeds_cfg,
    max_age_hours=config.max_age_hours,
    max_items=config.max_items_per_feed,
    on_feed_done=_persist_feed,
    clean_url_fn=clean_url if config.url_cleanup.enabled else None,
    fetch_settings=FetchSettings.from_config(config),
  )
  return articles, new_count_total


async def _run_summarize(
  need_llm: list[Article],
  translate_set: set[str],
  summarize_set: set[str],
  config: AppConfig,
  *,
  log_prefix: str = "Processing",
) -> SummarizeOutcome:
  """Invoke the LLM to fill summary/translation fields on `need_llm` in place.

  Shared by the bulk refresh path and the background `summarize_pending` path.
  """
  short_set = {
    a.url for a in need_llm if a.content_snippet and len(a.content_snippet) < SHORT_SNIPPET_CHARS
  }
  model = config.ollama.model
  base_url = os.environ.get("OLLAMA_BASE_URL") or config.ollama.base_url
  timeout = config.ollama.timeout
  log.step(f"{log_prefix} {len(need_llm)} articles with {model}...")
  outcome: SummarizeOutcome = await summarize_all(
    need_llm,
    model=model,
    base_url=base_url,
    timeout=timeout,
    translate_set=translate_set,
    summarize_set=summarize_set,
    short_set=short_set,
    native_lang=config.native_lang,
  )
  if outcome.failures:
    log.warn(f"  Done ({outcome.failures}/{len(need_llm)} failed).")
  else:
    log.success("  Done.")
  return outcome


@asynccontextmanager
async def _wait_for_summarize_lock() -> AsyncIterator[None]:
  """Hold the cross-process LLM lock, polling until it is free.

  Polls with try-lock instead of a blocking pg_advisory_lock so the web event
  loop never stalls and a cancellation lands at the next sleep.
  """
  waited = False
  while True:
    with repo.advisory_lock(repo.SUMMARIZE_LOCK_KEY) as got:
      if got:
        yield
        return
    if not waited:
      log.info("  Waiting for the LLM lock held by another process...")
      waited = True
    await asyncio.sleep(SUMMARIZE_LOCK_POLL_SECONDS)


async def _run_llm_pass(
  articles: list[Article],
  feeds: list[FeedRow],
  config: AppConfig,
) -> int:
  """Summarize/translate the subset of `articles` that still needs LLM work.
  Returns failure count.

  `articles` only narrows the candidates. Under the LLM lock the rows are
  re-read from the DB, which drops ones the background summarizer finished
  while we waited and picks up the latest stored body (another feed carrying
  the same URL may have rewritten it after this copy was taken).
  """
  summarize_map = {f.id: f.summarize for f in feeds}
  translate_map = {f.id: f.translate for f in feeds}
  # _needs_llm internalizes the feed-level gate: a feed with neither translate
  # nor summarize yields False for every article, so no separate gate is needed.
  candidate_urls = [
    a.url
    for a in articles
    if _needs_llm(
      a,
      translate=a.feed_id is not None and translate_map.get(a.feed_id, False),
      summarize=a.feed_id is not None and summarize_map.get(a.feed_id, False),
    )
  ]
  if candidate_urls:
    async with _wait_for_summarize_lock():
      pending = await asyncio.to_thread(
        repo.list_pending_summaries,
        limit=None,
        urls=candidate_urls,
      )
      if pending:
        rows_outcome = await _summarize_rows(pending, config, log_prefix="Processing")
        return rows_outcome.failures
  log.success("  All articles already processed.")
  return 0


async def _backfill_site_urls(feeds: list[FeedRow], fetch_settings: FetchSettings) -> None:
  """Look up `site_url` for any feed missing it by fetching the feed URL and
  scraping its declared site link. Runs feeds in parallel (capped) to keep
  refresh latency from scaling linearly with the missing count.

  Web feeds are skipped: their `url` already is the site page, and an HTML
  page rarely declares a feed-style link, so they would be re-fetched on
  every refresh for nothing.
  """
  missing: list[tuple[FeedRow, str]] = [
    (f, f.url) for f in feeds if not f.site_url and f.url and f.extraction_rules is None
  ]
  if not missing:
    return
  log.step(f"Backfilling site_url for {len(missing)} feeds...")
  sem = asyncio.Semaphore(8)

  async def _one(client: SafeClient, f: FeedRow, url: str) -> None:
    async with sem:
      try:
        resp = await client.get(url)
        resp.raise_for_status()
        site_url = extract_site_url(resp.text)
        if site_url:
          await asyncio.to_thread(repo.update_feed_site_url, f.id, site_url)
      except Exception as exc:
        # Best effort: one feed failing must not stop the others or the refresh.
        log.warn(f"  site_url backfill failed for {f.name}: {exc}")

  async with SafeClient(fetch_settings, timeout=15) as client:
    await asyncio.gather(*(_one(client, f, url) for f, url in missing))


async def refresh_all(
  config: AppConfig,
  *,
  source: str = "cron",
  no_summary: bool = False,
  job_id: int | None = None,
  force: bool = False,
) -> RefreshResult:
  """Fetch enabled feeds, summarize, and persist to DB.

  Single entry point used by both CLI (`main.py`) and Web (`POST /api/refresh`).
  When `job_id` is provided the caller already created the `refresh_jobs` row
  before returning (so clients observe running=true immediately after the HTTP
  response); otherwise it is created here.

  `force=True` bypasses the due-time filter and fetches every enabled feed
  (used by the manual Refresh button). `force=False` only fetches feeds whose
  `next_fetch_at` has passed or is NULL.

  Concurrent runs are serialized by a Postgres advisory lock so cron and a
  user-triggered web refresh cannot overlap; a blocked run closes its job as
  "skipped" and returns immediately.
  """
  if job_id is None:
    job_id = await asyncio.to_thread(repo.create_refresh_job, source)
  result = RefreshResult()

  # The try/except wraps the advisory_lock context itself so a failure while
  # acquiring the lock (rare DB error) still closes the refresh_jobs row.
  try:
    with repo.advisory_lock() as got_lock:
      if not got_lock:
        log.info("Refresh skipped: another refresh is already running.")
        await asyncio.to_thread(
          repo.finish_refresh_job,
          job_id,
          "skipped",
          0,
          "another refresh is running",
        )
        return result

      feeds = await asyncio.to_thread(repo.list_due_feeds, force)
      if not feeds:
        if force:
          log.warn("No enabled feeds found.")
        else:
          log.info("No feeds are due for fetch.")
        await asyncio.to_thread(repo.finish_refresh_job, job_id, "completed", 0, None)
        return result

      feeds_cfg = [f.to_feed_cfg() for f in feeds]

      log.step(f"Fetching feeds... ({len(feeds)} due, force={force})")
      articles, new_count = await _fetch_and_persist_feeds(
        feeds_cfg,
        config,
      )
      log.info(f"  {len(articles)} articles total, {new_count} new saved.")
      result.total_articles = len(articles)
      result.new_count = new_count

      if not articles:
        log.warn("No articles found.")
        await asyncio.to_thread(repo.finish_refresh_job, job_id, "completed", 0, None)
        return result

      restored = await asyncio.to_thread(
        enrich_from_legacy_cache,
        articles,
        Path(config.cache_dir),
      )
      if restored:
        # The LLM pass re-reads rows from the DB, so store these first.
        await asyncio.to_thread(repo.upsert_articles, restored)

      if no_summary or not config.ollama.enabled:
        if not no_summary:
          log.info("  Skipping LLM processing: ollama.enabled=false in config.")
      else:
        result.summarize_failures = await _run_llm_pass(articles, feeds, config)

      await _backfill_site_urls(feeds, FetchSettings.from_config(config))

      pruned = await asyncio.to_thread(repo.prune_articles, config.article_cache_max_age_days)
      if pruned:
        log.info(f"  Pruned {pruned} old articles.")

      await asyncio.to_thread(repo.finish_refresh_job, job_id, "completed", new_count, None)
      return result

  except Exception as exc:
    await asyncio.to_thread(repo.finish_refresh_job, job_id, "failed", 0, str(exc))
    raise


async def summarize_pending(config: AppConfig, batch_size: int = 5) -> int:
  """Process a batch of pending articles (feed.summarize/translate on, LLM
  output missing). Skips the round while another process holds the LLM lock.

  Returns the number of articles completed.
  """
  if not config.ollama.enabled:
    return 0
  with repo.advisory_lock(repo.SUMMARIZE_LOCK_KEY) as got:
    if not got:
      log.info("  Background summarizer skipped (LLM work running elsewhere).")
      return 0
    pending = await asyncio.to_thread(repo.list_pending_summaries, limit=batch_size)
    if not pending:
      return 0
    outcome = await _summarize_rows(pending, config, log_prefix="Background processing")
    return outcome.completed


@dataclass
class _RowsOutcome:
  completed: int
  failures: int


async def _summarize_rows(
  rows: list[ArticleRow],
  config: AppConfig,
  *,
  log_prefix: str,
) -> _RowsOutcome:
  """Run the LLM over pending DB rows and store its output. Callers must hold
  the LLM lock through the store.

  Every row the LLM leaves pending counts one failed attempt toward
  repo.LLM_MAX_FAILURES, so an article it can't finish stops being retried.
  Rows that failed on Ollama's side (unreachable, HTTP error) are spared.
  """
  feeds = await asyncio.to_thread(repo.list_feeds, enabled=True)
  feed_translate = {f.id: f.translate for f in feeds}
  feed_summarize = {f.id: f.summarize for f in feeds}

  articles = [
    Article(
      title=row.title,
      url=row.url,
      source=row.source,
      published=row.published,
      content_snippet=row.content_snippet or "",
      title_translated=row.title_translated or "",
      summary=row.summary or "",
      content_html=row.content_html or "",
      content_translated=row.content_translated or "",
      feed_id=row.feed_id,
    )
    for row in rows
  ]
  # Keyed by URL, not feed name: names aren't unique, and old rows keep the
  # name the feed had when they were fetched.
  translate_set: set[str] = {
    row.url for row in rows if row.feed_id and feed_translate.get(row.feed_id, False)
  }
  summarize_set: set[str] = {
    row.url for row in rows if row.feed_id and feed_summarize.get(row.feed_id, False)
  }

  def needs_llm(a: Article) -> bool:
    return _needs_llm(a, translate=a.url in translate_set, summarize=a.url in summarize_set)

  need_llm = [a for a in articles if needs_llm(a)]
  outcome = SummarizeOutcome()
  if need_llm:
    outcome = await _run_summarize(
      need_llm,
      translate_set,
      summarize_set,
      config,
      log_prefix=log_prefix,
    )
  for article in need_llm:
    if article.title_translated or article.summary or article.content_translated:
      await asyncio.to_thread(
        repo.update_article_summary,
        article.url,
        article.title_translated or None,
        article.summary or None,
        article.content_translated or None,
      )

  completed: list[str] = [a.url for a in need_llm if not needs_llm(a)]
  # Still pending after the LLM ran: an error or a partial answer.
  failed: list[str] = [
    a.url for a in need_llm if a.url not in outcome.ollama_errors and needs_llm(a)
  ]
  if failed:
    given_up = await asyncio.to_thread(repo.record_llm_failures, failed)
    if given_up:
      log.warn(
        f"  Giving up LLM processing for {len(given_up)} articles "
        f"after {repo.LLM_MAX_FAILURES} failed attempts."
      )
  return _RowsOutcome(completed=len(completed), failures=outcome.failures)
