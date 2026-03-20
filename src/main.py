import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

import cache as cache_mod
from feeds import fetch_all
from renderer import render_report
from summarizer import summarize_all


def load_config(path: Path) -> dict:
  with open(path, encoding="utf-8") as f:
    return yaml.safe_load(f)


async def run(args: argparse.Namespace) -> None:
  config = load_config(args.config)
  now = datetime.now(tz=timezone.utc)

  # Apply config to cache module
  cache_dir = Path(config.get("cache_dir", "cache"))
  cache_mod.CACHE_DIR = cache_dir
  cache_mod.FEED_CACHE_DIR = cache_dir / "feeds"
  cache_mod.ARTICLE_CACHE_PATH = cache_dir / "articles.json"
  cache_mod.ARTICLE_CACHE_MAX_AGE_DAYS = config.get("article_cache_max_age_days", 30)

  max_age_hours = config.get("max_age_hours", 25)
  output_dir = Path(config.get("output_dir", "output"))
  ollama_cfg = config.get("ollama", {})

  # 1. Fetch feeds (L1 cache: feed-level ETag/hash)
  print("Fetching feeds...")
  articles = await fetch_all(config["feeds"], max_age_hours=max_age_hours)
  print(f"  {len(articles)} articles total.")

  if not articles:
    print("No articles found. Exiting.")
    sys.exit(1)

  # 2. Restore L2 cache (article-level title_ja + summary)
  articles = cache_mod.restore_article_cache(articles)

  # 3. Summarize only articles without cached translations
  if not args.no_summary:
    need_summary = [a for a in articles if not a.title_ja or not a.summary]
    if need_summary:
      model = ollama_cfg.get("model", "gemma3")
      base_url = ollama_cfg.get("base_url", "http://localhost:11434")
      timeout = ollama_cfg.get("timeout", 120)
      print(f"Summarizing {len(need_summary)} new articles with {model}...")
      await summarize_all(
        need_summary, model=model, base_url=base_url, timeout=timeout,
      )
      print("  Done.")
    else:
      print("  All articles already have translations.")

  # 4. Save L2 cache (always, so --no-summary runs also persist restored data)
  cache_mod.save_article_cache(articles)

  # 5. Render (use JST for filename)
  JST = timezone(timedelta(hours=9))
  now_jst = now.astimezone(JST)
  output_path = output_dir / f"{now_jst.strftime('%Y-%m-%d')}.html"
  render_report(articles, output_path, date=now)
  print(f"Report: {output_path}")


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
