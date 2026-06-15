"""Pydantic v2 models for API DTOs, DB rows, and configuration."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


# -- DB row models --


class FilterRow(BaseModel):
  id: int = 0
  pattern: str
  target: str = "title"
  created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class FolderRow(BaseModel):
  id: int = 0
  name: str
  position: int = 0
  created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class FeedRow(BaseModel):
  id: int = 0
  name: str
  url: str | None = None
  site_url: str | None = None
  translate: bool = False
  summarize: bool = True
  enabled: bool = True
  folder_id: int | None = None
  position: int = 0
  last_fetched_at: datetime | None = None
  consecutive_failures: int = 0
  last_error: str | None = None
  extraction_rules: str | None = None
  fetch_interval_min: int = 10
  next_fetch_at: datetime | None = None
  created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))

  def to_feed_cfg(self) -> dict[str, object]:
    """Convert to the dict format expected by feeds.fetch_all()."""
    cfg: dict[str, object] = {
      "id": self.id,
      "name": self.name,
      "url": self.url,
    }
    if self.extraction_rules:
      cfg["extraction_rules"] = self.extraction_rules
    return cfg


class ArticleRow(BaseModel):
  url: str
  feed_id: int | None = None
  title: str
  title_translated: str | None = None
  source: str
  published: datetime | None = None
  content_snippet: str | None = None
  summary: str | None = None
  content_html: str | None = None
  content_translated: str | None = None
  fetched_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
  read_at: datetime | None = None


class RefreshJob(BaseModel):
  id: int = 0
  started_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
  finished_at: datetime | None = None
  source: str = "web"
  status: str = "running"
  new_count: int | None = None
  error: str | None = None


class FeedStats(BaseModel):
  article_count: int
  unread_count: int
  latest_published: datetime | None = None
  oldest_published: datetime | None = None


class StatusResponse(BaseModel):
  running: bool
  last_started_at: datetime | None = None
  last_finished_at: datetime | None = None
  last_status: str | None = None
  last_new_count: int | None = None
  last_error: str | None = None
  llm_enabled: bool = True
  llm_available: bool = True
  llm_error: str | None = None


# -- Config models --


class OllamaConfig(BaseModel):
  enabled: bool = True
  model: str = "gemma4:e4b"
  base_url: str = "http://localhost:11434"
  timeout: int = 120


class UrlCleanupConfig(BaseModel):
  enabled: bool = True


class AppConfig(BaseModel):
  max_items_per_feed: int = 200
  max_age_hours: float = 48
  cache_dir: str = "cache"
  article_cache_max_age_days: int = 0
  native_lang: str = "ja"
  ollama: OllamaConfig = Field(default_factory=OllamaConfig)
  url_cleanup: UrlCleanupConfig = Field(default_factory=UrlCleanupConfig)

  @classmethod
  def load(cls, path: Path = Path("config.yml"), *, required: bool = False) -> "AppConfig":
    """Read and validate config.yml. The single source of truth for all
    callers (web, fetcher, healthcheck).

    A missing file yields defaults unless `required` (the fetcher needs a real
    config). A non-mapping YAML is always an error: a broken config must surface
    consistently rather than being silently treated as defaults by some callers.
    """
    if not path.exists():
      if required:
        raise FileNotFoundError(path)
      return cls()
    with open(path, encoding="utf-8") as f:
      try:
        raw: object = yaml.safe_load(f)
      except yaml.YAMLError as e:
        # Normalize syntax errors to ValueError so every caller can treat a
        # broken config as a single "invalid config" failure mode.
        raise ValueError(f"config file is not valid YAML: {path}: {e}") from e
    if not isinstance(raw, dict):
      raise ValueError(f"config file is not a valid YAML mapping: {path}")
    return cls.model_validate(raw)
