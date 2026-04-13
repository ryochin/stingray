"""CLI entry point for the news feed aggregator."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
import yaml

import cache as cache_mod
import db
import log
from fetcher import refresh_all
from schemas import AppConfig
from seed import seed_feeds_from_config


def load_config(path: Path) -> AppConfig:
  with open(path, encoding="utf-8") as f:
    raw: object = yaml.safe_load(f)
  if not isinstance(raw, dict):
    log.error(f"Error: config file is not a valid YAML mapping: {path}")
    sys.exit(1)
  return AppConfig.model_validate(raw)


async def run(args: argparse.Namespace) -> None:
  config = load_config(args.config)

  # Resolve paths relative to config file
  config_base = args.config.resolve().parent
  db_path = Path(config.db_path)
  if not db_path.is_absolute():
    db_path = config_base / db_path
  cache_dir = Path(config.cache_dir)
  if not cache_dir.is_absolute():
    cache_dir = config_base / cache_dir

  # Initialize DB and L1 cache
  db.configure(db_path)
  db.init_schema()
  cache_mod.configure(cache_dir)

  # Seed feeds on first run
  seed_feeds_from_config(config.feeds)

  # Update config with resolved paths
  config.cache_dir = str(cache_dir)

  # Run the unified fetch/summarize/persist pipeline
  result = await refresh_all(config, source="cron", no_summary=args.no_summary)
  log.success(f"Done. {result.new_count} new / {result.total_articles} total articles.")


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
