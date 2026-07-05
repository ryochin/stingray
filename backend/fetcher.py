"""Unified fetch + summarize + persist entry point for CLI and Web."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

import log
import repo
from feeds import extract_site_url, fetch_all  # type: ignore[import-untyped]
from models import Article
from schemas import DEFAULT_USER_AGENT, AppConfig, FeedRow
from seed import enrich_from_legacy_cache
from summarizer import summarize_all  # type: ignore[import-untyped]
from url_cleaner import clean_url

SHORT_SNIPPET_CHARS = 300


@dataclass
class RefreshResult:
  total_articles: int = 0
  new_count: int = 0
  summarize_failures: int = 0


def _build_feed_id_map(feeds: list[FeedRow]) -> dict[str, int]:
  return {f.name: f.id for f in feeds}


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


def _classify_outcome(
  source_tag: str | None,
  feed_kind: str,
  fetch_exc: Exception | None,
  persist_exc: Exception | None,
  inserted_count: int,
) -> tuple[repo.Outcome, str | None]:
  """Fold the 5 per-feed observations into a scheduler outcome + error string.

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
  if source_tag == "5xx-cache":
    return "degraded", "serving stale cache: origin returned HTTP 4xx/5xx (blocked?)"
  if source_tag == "net-cache":
    return "degraded", "serving stale cache: network error reaching origin"
  if source_tag == "304-empty":
    return "degraded", "304 Not Modified but no cached copy available"
  # Normal path: fresh body or clean 304 / unchanged / web success (None tag).
  return ("fresh" if inserted_count >= 1 else "miss"), None


async def _fetch_and_persist_feeds(
  feeds_cfg: list[dict[str, Any]],
  feed_id_map: dict[str, int],
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
          repo.upsert_articles, feed_articles, feed_id_map,
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
    outcome, error = _classify_outcome(
      source_tag, feed_kind, fetch_exc, persist_exc, inserted_count,
    )
    try:
      await asyncio.to_thread(
        repo.record_feed_attempt, feed_id, outcome, error=error,
      )
    except Exception as e:
      log.error(
        f"  [schedule-error] {feed_cfg.get('name')}: {type(e).__name__}: {e}"
      )

  articles = await fetch_all(
    feeds_cfg,
    max_age_hours=config.max_age_hours,
    max_items=config.max_items_per_feed,
    on_feed_done=_persist_feed,
    clean_url_fn=clean_url if config.url_cleanup.enabled else None,
    user_agent=config.user_agent,
  )
  return articles, new_count_total


async def _run_summarize(
  need_llm: list[Article],
  translate_set: set[str],
  summarize_set: set[str],
  config: AppConfig,
  *,
  log_prefix: str = "Processing",
) -> int:
  """Invoke the LLM to fill summary/translation fields on `need_llm` in place.

  Returns the number of articles the LLM failed on. Shared by the bulk refresh
  path and the background `summarize_pending` path.
  """
  short_set = {
    a.url for a in need_llm
    if a.content_snippet and len(a.content_snippet) < SHORT_SNIPPET_CHARS
  }
  model = config.ollama.model
  base_url = os.environ.get("OLLAMA_BASE_URL") or config.ollama.base_url
  timeout = config.ollama.timeout
  log.step(f"{log_prefix} {len(need_llm)} articles with {model}...")
  failures: int = await summarize_all(
    need_llm, model=model, base_url=base_url, timeout=timeout,
    translate_set=translate_set, summarize_set=summarize_set, short_set=short_set,
    native_lang=config.native_lang,
  )
  if failures:
    log.warn(f"  Done ({failures}/{len(need_llm)} failed).")
  else:
    log.success("  Done.")
  return failures


async def _run_llm_pass(
  articles: list[Article],
  feeds: list[FeedRow],
  feed_id_map: dict[str, int],
  config: AppConfig,
) -> int:
  """Summarize/translate the subset of `articles` that still needs LLM work
  and re-upsert them so the new fields persist. Returns failure count.
  """
  summarize_map = {f.name: f.summarize for f in feeds}
  translate_map = {f.name: f.translate for f in feeds}
  # _needs_llm internalizes the feed-level gate: a feed with neither translate
  # nor summarize yields False for every article, so no separate gate is needed.
  need_llm = [
    a for a in articles
    if _needs_llm(
      a,
      translate=translate_map.get(a.source, False),
      summarize=summarize_map.get(a.source, False),
    )
  ]
  if not need_llm:
    log.success("  All articles already processed.")
    return 0
  translate_set = {name for name, flag in translate_map.items() if flag}
  summarize_set = {name for name, flag in summarize_map.items() if flag}
  failures = await _run_summarize(need_llm, translate_set, summarize_set, config)
  # Re-upsert to persist LLM-filled fields. Rows already exist from the
  # per-feed pass; COALESCE in _UPSERT_ARTICLE only fills newly non-null ones.
  await asyncio.to_thread(repo.upsert_articles, need_llm, feed_id_map)
  return failures


async def _backfill_site_urls(
  feeds: list[FeedRow], user_agent: str = DEFAULT_USER_AGENT
) -> None:
  """Look up `site_url` for any feed missing it by fetching the feed URL and
  scraping its declared site link. Runs feeds in parallel (capped) to keep
  refresh latency from scaling linearly with the missing count.
  """
  missing = [f for f in feeds if not f.site_url and f.url]
  if not missing:
    return
  log.step(f"Backfilling site_url for {len(missing)} feeds...")
  sem = asyncio.Semaphore(8)

  async def _one(client: httpx.AsyncClient, f: FeedRow) -> None:
    if not f.url:
      return
    async with sem:
      try:
        resp = await client.get(f.url)
        resp.raise_for_status()
        site_url = extract_site_url(resp.text)
        if site_url:
          await asyncio.to_thread(repo.update_feed_site_url, f.id, site_url)
      except Exception:
        return

  async with httpx.AsyncClient(
    timeout=15, follow_redirects=True, headers={"User-Agent": user_agent}
  ) as client:
    await asyncio.gather(*(_one(client, f) for f in missing))


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
  synchronously (so clients observe running=true immediately after the HTTP
  response); otherwise it is created here.

  `force=True` bypasses the due-time filter and fetches every enabled feed
  (used by the manual Refresh button). `force=False` only fetches feeds whose
  `next_fetch_at` has passed or is NULL.

  Concurrent runs are serialized by a Postgres advisory lock so cron and a
  user-triggered web refresh cannot overlap; a blocked run closes its job as
  "skipped" and returns immediately.
  """
  if job_id is None:
    job_id = repo.create_refresh_job(source)
  result = RefreshResult()

  # The try/except wraps the advisory_lock context itself so a failure while
  # acquiring the lock (rare DB error) still closes the refresh_jobs row.
  try:
    with repo.advisory_lock() as got_lock:
      if not got_lock:
        log.info("Refresh skipped: another refresh is already running.")
        repo.finish_refresh_job(
          job_id, "skipped", 0, "another refresh is running",
        )
        return result

      feeds = await asyncio.to_thread(repo.list_due_feeds, force)
      if not feeds:
        if force:
          log.warn("No enabled feeds found.")
        else:
          log.info("No feeds are due for fetch.")
        repo.finish_refresh_job(job_id, "completed", 0, None)
        return result

      feeds_cfg = [f.to_feed_cfg() for f in feeds]
      feed_id_map = _build_feed_id_map(feeds)

      log.step(f"Fetching feeds... ({len(feeds)} due, force={force})")
      articles, new_count = await _fetch_and_persist_feeds(
        feeds_cfg, feed_id_map, config,
      )
      log.info(f"  {len(articles)} articles total, {new_count} new saved.")
      result.total_articles = len(articles)
      result.new_count = new_count

      if not articles:
        log.warn("No articles found.")
        repo.finish_refresh_job(job_id, "completed", 0, None)
        return result

      articles = enrich_from_legacy_cache(articles, Path(config.cache_dir))

      if no_summary or not config.ollama.enabled:
        if not no_summary:
          log.info("  Skipping LLM processing: ollama.enabled=false in config.")
      else:
        result.summarize_failures = await _run_llm_pass(
          articles, feeds, feed_id_map, config,
        )

      await _backfill_site_urls(feeds, config.user_agent)

      pruned = repo.prune_articles(config.article_cache_max_age_days)
      if pruned:
        log.info(f"  Pruned {pruned} old articles.")

      repo.finish_refresh_job(job_id, "completed", new_count, None)
      return result

  except Exception as exc:
    repo.finish_refresh_job(job_id, "failed", 0, str(exc))
    raise


async def summarize_pending(config: AppConfig, batch_size: int = 5) -> int:
  """Process articles that are pending (feed.summarize=True but not yet processed).

  Returns the number of articles processed.
  """
  if not config.ollama.enabled:
    return 0
  pending = repo.list_pending_summaries(limit=batch_size)
  if not pending:
    return 0

  feeds = repo.list_feeds(enabled=True)
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
    )
    for row in pending
  ]
  translate_set: set[str] = {
    row.source for row in pending
    if row.feed_id and feed_translate.get(row.feed_id, False)
  }
  summarize_set: set[str] = {
    row.source for row in pending
    if row.feed_id and feed_summarize.get(row.feed_id, False)
  }
  need_llm = [
    a for a in articles
    if _needs_llm(
      a, translate=a.source in translate_set, summarize=a.source in summarize_set
    )
  ]

  if need_llm:
    await _run_summarize(
      need_llm, translate_set, summarize_set, config,
      log_prefix="Background processing",
    )

  for article in articles:
    if article.title_translated or article.summary or article.content_translated:
      repo.update_article_summary(
        article.url,
        article.title_translated or None,
        article.summary or None,
        article.content_translated or None,
      )

  return len(articles)
