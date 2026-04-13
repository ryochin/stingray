"""First-run seeding and legacy data migration."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

import log
import repo
from models import Article
from schemas import FeedRow


def seed_feeds_from_config(feeds_cfg: Sequence[Mapping[str, object]]) -> None:
  """Insert feeds from config.yml if the feeds table is empty."""
  existing = repo.list_feeds()
  if existing:
    return

  log.step("Seeding feeds from config.yml...")
  for cfg in feeds_cfg:
    feed = FeedRow(
      name=str(cfg.get("name", "")),
      type=str(cfg.get("type", "rss")),
      url=str(cfg["url"]) if "url" in cfg else None,
      subreddit=str(cfg["subreddit"]) if "subreddit" in cfg else None,
      sort=str(cfg["sort"]) if "sort" in cfg else None,
      lang=str(cfg.get("lang", "en")),
      max_items=int(cfg.get("max_items", 20)),  # type: ignore[arg-type]
      summarize=True,
      enabled=True,
    )
    fid = repo.add_feed(feed)
    log.info(f"  [{fid}] {feed.name} ({feed.type})")
  log.success(f"  Seeded {len(feeds_cfg)} feeds.")


def enrich_from_legacy_cache(
  articles: list[Article],
  cache_dir: Path,
) -> list[Article]:
  """Restore title_ja/summary from legacy articles.json for matching URLs.

  After enrichment, renames the file to .migrated so it's only processed once.
  """
  cache_path = cache_dir / "articles.json"
  if not cache_path.exists():
    return articles

  log.step("Migrating legacy article cache...")
  try:
    raw_text = cache_path.read_text(encoding="utf-8")
    parsed: object = json.loads(raw_text)
  except (json.JSONDecodeError, OSError) as e:
    log.warn(f"  Could not read legacy cache: {e}")
    return articles

  if not isinstance(parsed, dict):
    log.warn("  Legacy cache is not a dict, skipping.")
    return articles

  raw = cast(dict[str, object], parsed)
  legacy: dict[str, dict[str, str]] = {}
  for url_key, val in raw.items():
    if isinstance(val, dict):
      entry = cast(dict[str, object], val)
      legacy[url_key] = {
        str(k): str(v) for k, v in entry.items()
      }

  enriched = 0
  for article in articles:
    entry = legacy.get(article.url)
    if entry is None:
      continue
    if not article.title_ja and entry.get("title_ja"):
      article.title_ja = entry["title_ja"]
      enriched += 1
    if not article.summary and entry.get("summary"):
      article.summary = entry["summary"]

  if enriched:
    log.info(f"  Enriched {enriched} articles from legacy cache.")

  # Rename to .migrated (non-destructive)
  migrated_path = cache_path.with_suffix(".json.migrated")
  try:
    cache_path.rename(migrated_path)
    log.success(f"  Renamed {cache_path.name} → {migrated_path.name}")
  except OSError as e:
    log.warn(f"  Could not rename legacy cache: {e}")

  return articles
