"""Unified fetch + summarize + persist entry point for CLI and Web."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

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


async def refresh_all(
  config: AppConfig,
  *,
  source: str = "cron",
  no_summary: bool = False,
) -> RefreshResult:
  """Fetch all enabled feeds, summarize, and persist to DB.

  This is the single entry point used by both CLI (main.py) and Web (POST /api/refresh).
  """
  job_id = repo.create_refresh_job(source)
  result = RefreshResult()

  try:
    # 1. Load enabled feeds from DB
    feeds = repo.list_feeds(enabled=True)
    if not feeds:
      log.warn("No enabled feeds found.")
      repo.finish_refresh_job(job_id, "completed", 0, None)
      return result

    feeds_cfg = [f.to_feed_cfg() for f in feeds]
    feed_id_map = _build_feed_id_map(feeds)
    summarize_map = {f.name: f.summarize for f in feeds}
    translate_map = {f.name: f.translate for f in feeds}

    # 2. Fetch articles
    log.step("Fetching feeds...")
    articles, feed_results = await fetch_all(
      feeds_cfg, max_age_hours=config.max_age_hours, max_items=config.max_items_per_feed,
    )
    for feed_id, success, error_msg in feed_results:
      repo.update_feed_fetch_status(feed_id, success=success, error=error_msg)

    log.info(f"  {len(articles)} articles total.")
    result.total_articles = len(articles)

    if not articles:
      log.warn("No articles found.")
      repo.finish_refresh_job(job_id, "completed", 0, None)
      return result

    # 3. Enrich from legacy cache (one-time migration)
    cache_dir = Path(config.cache_dir)
    articles = enrich_from_legacy_cache(articles, cache_dir)

    # 4. LLM processing (respecting per-feed summarize + translate settings)
    if not no_summary:
      need_llm = [
        a for a in articles
        if summarize_map.get(a.source, False)
        and _needs_llm(a, translate=translate_map.get(a.source, False))
      ]

      # Build sets for summarize_all
      translate_set = {name for name, flag in translate_map.items() if flag}
      short_set = {
        a.url for a in need_llm
        if a.content_snippet and len(a.content_snippet) < SHORT_SNIPPET_CHARS
      }

      if need_llm:
        model = config.ollama.model
        base_url = os.environ.get("OLLAMA_BASE_URL") or config.ollama.base_url
        timeout = config.ollama.timeout
        log.step(f"Processing {len(need_llm)} articles with {model}...")
        failures = await summarize_all(
          need_llm, model=model, base_url=base_url, timeout=timeout,
          translate_set=translate_set, short_set=short_set,
          native_lang=config.native_lang,
        )
        result.summarize_failures = failures
        if failures:
          log.warn(f"  Done ({failures}/{len(need_llm)} failed).")
        else:
          log.success("  Done.")
      else:
        log.success("  All articles already processed.")

    # 5. Backfill site_url for feeds missing it
    feeds_needing_site_url = [f for f in feeds if not f.site_url and f.url]
    if feeds_needing_site_url:
      log.step(f"Backfilling site_url for {len(feeds_needing_site_url)} feeds...")
      async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        for f in feeds_needing_site_url:
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

    # 6. Persist to DB
    new_count = repo.upsert_articles(articles, feed_id_map)
    result.new_count = new_count
    log.info(f"  {new_count} new articles saved.")

    # 7. Prune old articles
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
  pending = repo.list_pending_summaries(limit=batch_size)
  if not pending:
    return 0

  # Build translate map from feeds
  feeds = repo.list_feeds(enabled=True)
  feed_translate = {f.id: f.translate for f in feeds}

  # Convert ArticleRow to Article for summarizer
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

  # Determine which articles need translation based on feed
  translate_set: set[str] = set()
  for row in pending:
    if row.feed_id and feed_translate.get(row.feed_id, False):
      translate_set.add(row.source)

  # Filter to articles that actually need LLM
  need_llm = [
    a for a in articles
    if _needs_llm(a, translate=a.source in translate_set)
  ]

  if need_llm:
    short_set = {
      a.url for a in need_llm
      if a.content_snippet and len(a.content_snippet) < SHORT_SNIPPET_CHARS
    }
    model = config.ollama.model
    base_url = os.environ.get("OLLAMA_BASE_URL") or config.ollama.base_url
    timeout = config.ollama.timeout
    log.step(f"Background processing {len(need_llm)} articles with {model}...")
    await summarize_all(
      need_llm, model=model, base_url=base_url, timeout=timeout,
      translate_set=translate_set, short_set=short_set,
      native_lang=config.native_lang,
    )

  # Persist results
  for article in articles:
    if article.title_translated or article.summary or article.content_translated:
      repo.update_article_summary(
        article.url,
        article.title_translated or None,
        article.summary or None,
        article.content_translated or None,
      )

  return len(articles)
