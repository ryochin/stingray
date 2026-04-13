"""FastAPI application — JSON API + SPA fallback."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import yaml
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, model_validator

import db
import repo
from fetcher import refresh_all
from schemas import AppConfig, ArticleRow, FeedRow, StatusResponse
from seed import seed_feeds_from_config

# -- Globals for background refresh --
_refresh_lock = asyncio.Lock()
_refresh_task: asyncio.Task[None] | None = None


def _load_config() -> AppConfig:
  config_path = Path("config.yml")
  if not config_path.exists():
    return AppConfig()
  with open(config_path, encoding="utf-8") as f:
    raw: object = yaml.safe_load(f)
  if isinstance(raw, dict):
    return AppConfig.model_validate(raw)
  return AppConfig()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
  config = _load_config()
  db.configure(config.db_path)
  db.init_schema()
  seed_feeds_from_config(config.feeds)
  app.state.config = config  # type: ignore[attr-defined]
  yield


app = FastAPI(title="News Reader", lifespan=lifespan)


# -- Articles --


@app.get("/api/articles")
def get_articles(
  feed_id: int | None = Query(None),
  limit: int = Query(500, ge=1, le=5000),
) -> list[ArticleRow]:
  return repo.list_articles(feed_id=feed_id, limit=limit)


# -- Feeds --


class FeedCreate(BaseModel):
  name: str
  type: str = "rss"
  url: str | None = None
  subreddit: str | None = None
  sort: str | None = None
  lang: str = "en"
  max_items: int = 20
  summarize: bool = True

  @model_validator(mode="after")
  def _check_url_or_subreddit(self) -> FeedCreate:
    if self.type == "rss" and not self.url:
      raise ValueError("RSS feeds require a URL")
    if self.type == "reddit" and not self.subreddit:
      raise ValueError("Reddit feeds require a subreddit")
    return self


@app.get("/api/feeds")
def get_feeds() -> list[FeedRow]:
  return repo.list_feeds()


@app.post("/api/feeds", status_code=201)
def create_feed(body: FeedCreate) -> FeedRow:
  feed = FeedRow(**body.model_dump())
  feed_id = repo.add_feed(feed)
  feeds = repo.list_feeds()
  for f in feeds:
    if f.id == feed_id:
      return f
  raise HTTPException(404, "Feed not found after creation")


@app.delete("/api/feeds/{feed_id}", status_code=204)
def delete_feed(feed_id: int) -> None:
  repo.delete_feed(feed_id)


@app.post("/api/feeds/{feed_id}/toggle")
def toggle_feed(feed_id: int) -> FeedRow:
  repo.toggle_feed(feed_id)
  updated = next((f for f in repo.list_feeds() if f.id == feed_id), None)
  if updated is None:
    raise HTTPException(404, "Feed not found")
  return updated


@app.post("/api/feeds/{feed_id}/summarize")
def toggle_summarize(feed_id: int) -> FeedRow:
  repo.toggle_summarize(feed_id)
  updated = next((f for f in repo.list_feeds() if f.id == feed_id), None)
  if updated is None:
    raise HTTPException(404, "Feed not found")
  return updated


# -- Refresh --


async def _run_refresh(config: AppConfig) -> None:
  global _refresh_task
  try:
    await refresh_all(config, source="web")
  finally:
    _refresh_task = None


@app.post("/api/refresh")
async def trigger_refresh(request: Request) -> JSONResponse:
  global _refresh_task
  async with _refresh_lock:
    if _refresh_task is not None and not _refresh_task.done():
      return JSONResponse({"message": "Refresh already in progress"}, status_code=409)
    config: AppConfig = request.app.state.config  # type: ignore[attr-defined]
    _refresh_task = asyncio.create_task(_run_refresh(config))
  return JSONResponse({"message": "Refresh started"}, status_code=202)


@app.get("/api/status")
def get_status() -> StatusResponse:
  job = repo.get_latest_refresh_job()
  if job is None:
    return StatusResponse(running=False)
  return StatusResponse(
    running=job.status == "running",
    last_started_at=job.started_at,
    last_finished_at=job.finished_at,
    last_status=job.status,
    last_new_count=job.new_count,
    last_error=job.error,
  )


@app.get("/api/health")
def health() -> dict[str, str]:
  return {"status": "ok"}


# -- SPA fallback --
# Mount built frontend if it exists, otherwise skip.

_frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if _frontend_dist.is_dir():
  app.mount("/assets", StaticFiles(directory=str(_frontend_dist / "assets")), name="static")

  @app.get("/{path:path}")
  async def spa_fallback(path: str) -> FileResponse:
    return FileResponse(str(_frontend_dist / "index.html"))
