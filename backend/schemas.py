"""Pydantic v2 models for API DTOs, DB rows, and configuration."""

from __future__ import annotations

import contextlib
import ipaddress
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

# -- DB row models --


# Explicit fetch-health classification surfaced to the API. Unlike
# consecutive_failures (which measures severity of a failure streak), this
# captures the *most recent* fetch outcome so the UI can distinguish a soft
# degraded/stale-cache state from a hard failure without heuristics.
FeedHealth = Literal["ok", "degraded", "failing"]


class FilterRow(BaseModel):
  id: int = 0
  pattern: str
  target: str = "title"
  created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


class FolderRow(BaseModel):
  id: int = 0
  name: str
  position: int = 0
  created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


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
  health: FeedHealth = "ok"
  extraction_rules: str | None = None
  fetch_interval_min: int = 10
  next_fetch_at: datetime | None = None
  created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))

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
  fetched_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
  read_at: datetime | None = None


class RefreshJob(BaseModel):
  id: int = 0
  started_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
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


# Some origins (e.g. exblog / CDN WAFs) reject the default httpx User-Agent
# with 403; a browser-like UA is required to fetch feeds and pages at all.
DEFAULT_USER_AGENT = (
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
  "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0"
)


class OllamaConfig(BaseModel):
  model_config = ConfigDict(extra="forbid")

  enabled: bool = True
  model: str = "gemma4:e4b"
  base_url: str = "http://localhost:11434"
  timeout: int = 120


class UrlCleanupConfig(BaseModel):
  model_config = ConfigDict(extra="forbid")

  enabled: bool = True


class SelectorInferenceConfig(BaseModel):
  model_config = ConfigDict(extra="forbid")

  # Guard against config typos that would silently break inference: e.g.
  # min_articles=0 accepts a zero-article match, max_attempts=0 never calls the LLM.
  max_html_bytes: int = Field(default=150000, ge=1000)
  max_attempts: int = Field(default=4, ge=1)
  num_ctx: int = Field(default=65536, ge=512)
  # A real article index almost always lists 3+ posts; requiring 3 rejects small
  # related/ranking side-lists that a 2-row match would otherwise accept.
  min_articles: int = Field(default=3, ge=1)


class FetchConfig(BaseModel):
  model_config = ConfigDict(extra="forbid")

  # Feed URLs arrive through an unauthenticated API, so destinations that
  # resolve to non-public addresses (loopback, LAN, cloud metadata, the compose
  # network) are refused unless listed here. Entries are hostnames (exact,
  # case-insensitive), IP addresses, or CIDR networks.
  allow_private: list[str] = Field(default_factory=list)
  # Upper bound on a decoded response body; larger downloads are aborted.
  max_response_bytes: int = Field(default=10 * 1024 * 1024, ge=64 * 1024)

  @field_validator("allow_private")
  @classmethod
  def _normalize_allow_private(cls, entries: list[str]) -> list[str]:
    normalized: list[str] = []
    for entry in entries:
      value = entry.strip().lower()
      if value.startswith("[") and value.endswith("]"):
        # Bracketed IPv6 literal, as written in URLs; brackets around anything
        # else are rejected as a non-bare host below.
        with contextlib.suppress(ValueError):
          value = str(ipaddress.IPv6Address(value[1:-1]))
      if not value:
        raise ValueError("allow_private entries must not be empty")
      if "/" in value:
        # Anything with a slash must be a valid CIDR, so a typo cannot
        # silently degrade into a never-matching hostname.
        ipaddress.ip_network(value, strict=False)
      else:
        try:
          ipaddress.ip_address(value)
        except ValueError:
          # A port, userinfo, or path would be silently dropped below and
          # widen the entry to the whole host, so reject them outright.
          if any(c in value for c in ":@?#[]\\ "):
            raise ValueError(f"allow_private entry must be a bare host: {entry!r}") from None
          # Store hostnames in the IDNA form httpx puts on the wire, so a
          # Unicode entry matches a Punycode URL and vice versa.
          try:
            value = httpx.URL(f"http://{value}/").raw_host.decode("ascii")
          except httpx.InvalidURL as e:
            raise ValueError(f"invalid allow_private host: {entry!r}") from e
      normalized.append(value)
    return normalized


class AppConfig(BaseModel):
  model_config = ConfigDict(extra="forbid")

  max_items_per_feed: int = 200
  max_age_hours: float = 48
  cache_dir: str = "cache"
  article_cache_max_age_days: int = 0
  native_lang: str = "en"
  article_order: Literal["oldest", "newest"] = "oldest"
  user_agent: str = DEFAULT_USER_AGENT
  ollama: OllamaConfig = Field(default_factory=OllamaConfig)
  url_cleanup: UrlCleanupConfig = Field(default_factory=UrlCleanupConfig)
  selector_inference: SelectorInferenceConfig = Field(default_factory=SelectorInferenceConfig)
  fetch: FetchConfig = Field(default_factory=FetchConfig)

  @classmethod
  def load(cls, path: Path = Path("config.yml"), *, required: bool = False) -> AppConfig:
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
