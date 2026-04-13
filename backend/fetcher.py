"""Unified fetch + summarize + persist entry point for CLI and Web."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
import log
import repo
from feeds import fetch_all  # type: ignore[import-untyped]
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


def _should_summarize(article: Article) -> bool:
  """Check if an article needs LLM processing."""
  if article.summary and (article.lang == "ja" or article.title_ja):
    return False
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

    # 2. Fetch articles
    log.step("Fetching feeds...")
    articles = await fetch_all(feeds_cfg, max_age_hours=config.max_age_hours)
    log.info(f"  {len(articles)} articles total.")
    result.total_articles = len(articles)

    if not articles:
      log.warn("No articles found.")
      repo.finish_refresh_job(job_id, "completed", 0, None)
      return result

    # 3. Enrich from legacy cache (one-time migration)
    cache_dir = Path(config.cache_dir)
    articles = enrich_from_legacy_cache(articles, cache_dir)

    # 4. Summarize (respecting per-feed summarize setting)
    if not no_summary:
      # Short ja snippets: reuse as summary
      for a in articles:
        if (
          a.lang == "ja"
          and not a.summary
          and a.content_snippet
          and len(a.content_snippet) < SHORT_SNIPPET_CHARS
        ):
          a.summary = a.content_snippet

      need_summary = [
        a for a in articles
        if summarize_map.get(a.source, False)
        and _should_summarize(a)
      ]

      if need_summary:
        model = config.ollama.model
        base_url = os.environ.get("OLLAMA_BASE_URL") or config.ollama.base_url
        timeout = config.ollama.timeout
        log.step(f"Summarizing {len(need_summary)} articles with {model}...")
        failures = await summarize_all(
          need_summary, model=model, base_url=base_url, timeout=timeout,
        )
        result.summarize_failures = failures
        if failures:
          log.warn(f"  Done ({failures}/{len(need_summary)} failed).")
        else:
          log.success("  Done.")
      else:
        log.success("  All articles already have translations.")

    # 5. Persist to DB
    new_count = repo.upsert_articles(articles, feed_id_map)
    result.new_count = new_count
    log.info(f"  {new_count} new articles saved.")

    # 6. Prune old articles
    pruned = repo.prune_articles(config.article_cache_max_age_days)
    if pruned:
      log.info(f"  Pruned {pruned} old articles.")

    repo.finish_refresh_job(job_id, "completed", new_count, None)
    return result

  except Exception as e:
    repo.finish_refresh_job(job_id, "failed", 0, str(e))
    raise
