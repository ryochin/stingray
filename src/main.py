import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

import cache as cache_mod
import log
from feeds import fetch_all
from renderer import render_report
from summarizer import summarize_all

# For ja articles: snippets shorter than this are treated as already-summarized
# and displayed as-is, skipping the LLM call.
SHORT_SNIPPET_CHARS = 300


def load_config(path: Path) -> dict:
  with open(path, encoding="utf-8") as f:
    config = yaml.safe_load(f)
  if not isinstance(config, dict):
    log.error(f"Error: config file is not a valid YAML mapping: {path}")
    sys.exit(1)
  if "feeds" not in config or not isinstance(config["feeds"], list):
    log.error(f"Error: config must contain a 'feeds' list: {path}")
    sys.exit(1)
  for i, feed in enumerate(config["feeds"]):
    if not isinstance(feed, dict) or "name" not in feed:
      log.error(f"Error: feeds[{i}] must be a mapping with a 'name' key")
      sys.exit(1)
    ftype = feed.get("type", "rss")
    if ftype == "rss" and "url" not in feed:
      log.error(f"Error: feeds[{i}] ({feed['name']}): RSS feed requires 'url'")
      sys.exit(1)
    if ftype == "reddit" and "subreddit" not in feed:
      log.error(f"Error: feeds[{i}] ({feed['name']}): Reddit feed requires 'subreddit'")
      sys.exit(1)
  return config


async def run(args: argparse.Namespace) -> None:
  config = load_config(args.config)
  now = datetime.now(tz=timezone.utc)

  # Apply config to cache module (resolve relative to config file)
  config_base = args.config.resolve().parent
  cache_dir = Path(config.get("cache_dir", "cache"))
  if not cache_dir.is_absolute():
    cache_dir = config_base / cache_dir
  cache_mod.configure(cache_dir, config.get("article_cache_max_age_days", 30))

  max_age_hours = config.get("max_age_hours", 25)
  output_dir = Path(config.get("output_dir", "output"))
  if not output_dir.is_absolute():
    output_dir = config_base / output_dir
  ollama_cfg = config.get("ollama", {})

  # 1. Fetch feeds (L1 cache: feed-level ETag/hash)
  log.step("Fetching feeds...")
  articles = await fetch_all(config["feeds"], max_age_hours=max_age_hours)
  log.info(f"  {len(articles)} articles total.")

  if not articles:
    log.warn("No articles found. Skipping report generation.")
    return

  # 2. Restore L2 cache (article-level title_ja + summary)
  articles = cache_mod.restore_article_cache(articles)

  # 3. Summarize only articles without cached translations
  if not args.no_summary:
    # Short ja snippets already look like a summary — reuse them verbatim.
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
      if not a.summary or (a.lang != "ja" and not a.title_ja)
    ]
    if need_summary:
      model = ollama_cfg.get("model", "gemma3")
      base_url = os.environ.get("OLLAMA_BASE_URL") or ollama_cfg.get("base_url", "http://localhost:11434")
      timeout = ollama_cfg.get("timeout", 120)
      log.step(f"Summarizing {len(need_summary)} new articles with {model}...")
      failures = await summarize_all(
        need_summary, model=model, base_url=base_url, timeout=timeout,
      )
      if failures:
        log.warn(f"  Done ({failures}/{len(need_summary)} failed).")
      else:
        log.success("  Done.")
    else:
      log.success("  All articles already have translations.")

  # 4. Save L2 cache (always, so --no-summary runs also persist restored data)
  cache_mod.save_article_cache(articles)

  # 5. Render (use JST for filename)
  JST = timezone(timedelta(hours=9))
  now_jst = now.astimezone(JST)
  output_path = output_dir / f"{now_jst.strftime('%Y-%m-%d')}.html"
  render_report(articles, output_path, date=now_jst)
  log.success(f"Report: {output_path}")


def main() -> None:
  parser = argparse.ArgumentParser(description="News feed aggregator")
  parser.add_argument(
    "--config", type=Path, default=Path("config.yml"),
    help="Path to config file",
  )
  parser.add_argument(
    "--no-summary", action="store_true",
    help="Skip LLM summarization",
  )
  args = parser.parse_args()
  asyncio.run(run(args))


if __name__ == "__main__":
  main()
