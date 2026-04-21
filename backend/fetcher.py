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
from schemas import AppConfig, FeedRow
from seed import enrich_from_legacy_cache
from summarizer import summarize_all  # type: ignore[import-untyped]

SHORT_SNIPPET_CHARS = 300


@dataclass
class RefreshResult:
  total_articles: int = 0
  new_count: int = 0
  summarize_failures: int = 0


def _build_feed_id_map(feeds: list[FeedRow]) -> dict[str, int]:
  return {f.name: f.id for f in feeds}


def _needs_llm(article: Article, *, translate: bool) -> bool:
  """Check if an article needs LLM processing."""
  if translate:
    # Non-native: need both title_translated and (summary or content_translated)
    if article.title_translated and (article.summary or article.content_translated):
      return False
    return True
  else:
    # Native: only need summary for long content
    if article.summary:
      return False
    if article.content_snippet and len(article.content_snippet) < SHORT_SNIPPET_CHARS:
      return False  # Native + short → nothing to do
    return True


async def _fetch_and_persist_feeds(
  feeds_cfg: list[dict[str, Any]],
  feed_id_map: dict[str, int],
  config: AppConfig,
) -> tuple[list[Article], list[tuple[int, bool, str | None]], int]:
  """Fetch all feeds in parallel and upsert each feed's articles as soon as it
  finishes, so the web UI sees rows appear incrementally instead of waiting for
  every feed plus LLM processing. LLM-filled fields are populated later by a
  second upsert; `ON CONFLICT DO UPDATE COALESCE` makes that safe.
  """
  new_count_total = 0
  new_count_lock = asyncio.Lock()

  async def _persist_feed(feed_cfg: dict[str, Any], feed_articles: list[Article]) -> None:
    nonlocal new_count_total
    count = await asyncio.to_thread(repo.upsert_articles, feed_articles, feed_id_map)
    if count:
      async with new_count_lock:
        new_count_total += count
      name = feed_cfg.get("name") or "?"
      log.info(f"  [db] {name}: +{count} new")

  articles, feed_results = await fetch_all(
    feeds_cfg,
    max_age_hours=config.max_age_hours,
    max_items=config.max_items_per_feed,
    on_feed_done=_persist_feed,
  )
  return articles, feed_results, new_count_total


async def _run_summarize(
  need_llm: list[Article],
  translate_set: set[str],
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
    translate_set=translate_set, short_set=short_set,
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
  need_llm = [
    a for a in articles
    if summarize_map.get(a.source, False)
    and _needs_llm(a, translate=translate_map.get(a.source, False))
  ]
  if not need_llm:
    log.success("  All articles already processed.")
    return 0
  translate_set = {name for name, flag in translate_map.items() if flag}
  failures = await _run_summarize(need_llm, translate_set, config)
  # Re-upsert to persist LLM-filled fields. Rows already exist from the
  # per-feed pass; COALESCE in _UPSERT_ARTICLE only fills newly non-null ones.
  repo.upsert_articles(need_llm, feed_id_map)
  return failures


async def _backfill_site_urls(feeds: list[FeedRow]) -> None:
  """Look up `site_url` for any feed missing it by fetching the feed URL and
  scraping its declared site link.
  """
  missing = [f for f in feeds if not f.site_url and f.url]
  if not missing:
    return
  log.step(f"Backfilling site_url for {len(missing)} feeds...")
  async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
    for f in missing:
      if not f.url:
        continue
      try:
        resp = await client.get(f.url)
        resp.raise_for_status()
        site_url = extract_site_url(resp.text)
        if site_url:
          repo.update_feed_site_url(f.id, site_url)
      except Exception:
        continue


async def refresh_all(
  config: AppConfig,
  *,
  source: str = "cron",
  no_summary: bool = False,
  job_id: int | None = None,
) -> RefreshResult:
  """Fetch all enabled feeds, summarize, and persist to DB.

  Single entry point used by both CLI (`main.py`) and Web (`POST /api/refresh`).
  When `job_id` is provided the caller already created the `refresh_jobs` row
  synchronously (so clients observe running=true immediately after the HTTP
  response); otherwise it is created here.
  """
  if job_id is None:
    job_id = repo.create_refresh_job(source)
  result = RefreshResult()

  try:
    feeds = repo.list_feeds(enabled=True)
    if not feeds:
      log.warn("No enabled feeds found.")
      repo.finish_refresh_job(job_id, "completed", 0, None)
      return result

    feeds_cfg = [f.to_feed_cfg() for f in feeds]
    feed_id_map = _build_feed_id_map(feeds)

    log.step("Fetching feeds...")
    articles, feed_results, new_count = await _fetch_and_persist_feeds(
      feeds_cfg, feed_id_map, config,
    )
    for feed_id, success, error_msg in feed_results:
      repo.update_feed_fetch_status(feed_id, success=success, error=error_msg)
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

    await _backfill_site_urls(feeds)

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
  need_llm = [a for a in articles if _needs_llm(a, translate=a.source in translate_set)]

  if need_llm:
    await _run_summarize(need_llm, translate_set, config, log_prefix="Background processing")

  for article in articles:
    if article.title_translated or article.summary or article.content_translated:
      repo.update_article_summary(
        article.url,
        article.title_translated or None,
        article.summary or None,
        article.content_translated or None,
      )

  return len(articles)
