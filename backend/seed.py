"""Legacy data migration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import log
from models import Article


def enrich_from_legacy_cache(
  articles: list[Article],
  cache_dir: Path,
) -> list[Article]:
  """Restore title_translated/summary from legacy articles.json for matching URLs.

  Fills `articles` in place and returns the ones it changed; the caller must
  persist them, since the file is renamed to .migrated so it is only read once.
  """
  cache_path = cache_dir / "articles.json"
  if not cache_path.exists():
    return []

  log.step("Migrating legacy article cache...")
  try:
    raw_text = cache_path.read_text(encoding="utf-8")
    parsed: object = json.loads(raw_text)
  except (json.JSONDecodeError, OSError) as e:
    log.warn(f"  Could not read legacy cache: {e}")
    return []

  if not isinstance(parsed, dict):
    log.warn("  Legacy cache is not a dict, skipping.")
    return []

  raw = cast(dict[str, object], parsed)
  legacy: dict[str, dict[str, str]] = {}
  for url_key, val in raw.items():
    if isinstance(val, dict):
      entry = cast(dict[str, object], val)
      legacy[url_key] = {
        str(k): str(v) for k, v in entry.items()
      }

  restored: list[Article] = []
  for article in articles:
    entry = legacy.get(article.url)
    if entry is None:
      continue
    changed = False
    if not article.title_translated and entry.get("title_ja"):
      article.title_translated = entry["title_ja"]
      changed = True
    if not article.summary and entry.get("summary"):
      article.summary = entry["summary"]
      changed = True
    if changed:
      restored.append(article)

  if restored:
    log.info(f"  Enriched {len(restored)} articles from legacy cache.")

  # Rename to .migrated (non-destructive)
  migrated_path = cache_path.with_suffix(".json.migrated")
  try:
    cache_path.rename(migrated_path)
    log.success(f"  Renamed {cache_path.name} → {migrated_path.name}")
  except OSError as e:
    log.warn(f"  Could not rename legacy cache: {e}")

  return restored
