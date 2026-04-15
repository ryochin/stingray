"""Pydantic v2 models for API DTOs, DB rows, and configuration."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


# -- DB row models --


class NgWordRow(BaseModel):
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
  lang: str = "en"
  max_items: int = 20
  summarize: bool = True
  enabled: bool = True
  folder_id: int | None = None
  created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))

  def to_feed_cfg(self) -> dict[str, object]:
    """Convert to the dict format expected by feeds.fetch_all()."""
    return {
      "name": self.name,
      "url": self.url,
      "lang": self.lang,
      "max_items": self.max_items,
    }


class ArticleRow(BaseModel):
  url: str
  feed_id: int | None = None
  title: str
  title_ja: str | None = None
  source: str
  published: datetime | None = None
  content_snippet: str | None = None
  summary: str | None = None
  content_html: str | None = None
  lang: str | None = None
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


class StatusResponse(BaseModel):
  running: bool
  last_started_at: datetime | None = None
  last_finished_at: datetime | None = None
  last_status: str | None = None
  last_new_count: int | None = None
  last_error: str | None = None


# -- Config models --


class OllamaConfig(BaseModel):
  model: str = "gemma3"
  base_url: str = "http://localhost:11434"
  timeout: int = 120


class AppConfig(BaseModel):
  max_age_hours: float = 25
  db_path: str = "data/news.db"
  cache_dir: str = "cache"
  article_cache_max_age_days: int = 30
  ollama: OllamaConfig = Field(default_factory=OllamaConfig)
