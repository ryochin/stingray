"""FastAPI application — JSON API + SPA fallback."""

from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

import feedparser
import httpx
import yaml
from fastapi import FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, model_validator

import db
import repo
from feeds import _fetch_rss
from fetcher import refresh_all
from schemas import AppConfig, ArticleRow, FeedRow, FolderRow, NgWordRow, StatusResponse

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


async def _background_summarizer(config: AppConfig) -> None:
  """Periodically check for unsummarized articles and process them."""
  from fetcher import summarize_pending
  import log as _log
  await asyncio.sleep(10)  # initial delay
  while True:
    try:
      count = await summarize_pending(config)
      if count > 0:
        _log.info(f"  Background summarizer processed {count} articles.")
    except Exception as e:
      _log.warn(f"  Background summarizer error: {e}")
    await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
  config = _load_config()
  db.configure(config.db_path)
  db.init_schema()
  app.state.config = config  # type: ignore[attr-defined]
  task = asyncio.create_task(_background_summarizer(config))
  yield
  task.cancel()


app = FastAPI(title="News Reader", lifespan=lifespan)


# -- Articles --


@app.get("/api/articles")
def get_articles(
  feed_id: int | None = Query(None),
  unread: bool = Query(False),
  limit: int = Query(500, ge=1, le=5000),
) -> list[ArticleRow]:
  return repo.list_articles(feed_id=feed_id, unread=unread, limit=limit)


class ArticleUrls(BaseModel):
  urls: list[str]


@app.post("/api/articles/read", status_code=204)
def mark_articles_read(body: ArticleUrls) -> None:
  repo.mark_read(body.urls)


@app.post("/api/articles/unread", status_code=204)
def mark_articles_unread(body: ArticleUrls) -> None:
  repo.mark_unread(body.urls)


@app.post("/api/articles/read-all")
def mark_all_articles_read(feed_id: int | None = Query(None)) -> dict[str, int]:
  count = repo.mark_all_read(feed_id)
  return {"marked": count}


# -- Folders --


class FolderCreate(BaseModel):
  name: str


class FolderRename(BaseModel):
  name: str


class FolderReorder(BaseModel):
  folder_ids: list[int]


@app.get("/api/folders")
def get_folders() -> list[FolderRow]:
  return repo.list_folders()


@app.post("/api/folders", status_code=201)
def create_folder(body: FolderCreate) -> FolderRow:
  folder_id = repo.create_folder(body.name)
  folders = repo.list_folders()
  for f in folders:
    if f.id == folder_id:
      return f
  raise HTTPException(404, "Folder not found after creation")


@app.patch("/api/folders/{folder_id}")
def rename_folder(folder_id: int, body: FolderRename) -> FolderRow:
  repo.rename_folder(folder_id, body.name)
  folders = repo.list_folders()
  updated = next((f for f in folders if f.id == folder_id), None)
  if updated is None:
    raise HTTPException(404, "Folder not found")
  return updated


@app.delete("/api/folders/{folder_id}", status_code=204)
def delete_folder(folder_id: int) -> None:
  repo.delete_folder(folder_id)


@app.put("/api/folders/reorder", status_code=204)
def reorder_folders(body: FolderReorder) -> None:
  repo.reorder_folders(body.folder_ids)


# -- Feeds --


class FeedMove(BaseModel):
  folder_id: int | None = None


class FeedCreate(BaseModel):
  name: str = ""
  url: str
  lang: str = ""
  summarize: bool = False
  folder_id: int | None = None


@app.get("/api/feeds")
def get_feeds() -> list[FeedRow]:
  return repo.list_feeds()


@app.get("/api/feeds/stats")
def get_feed_stats() -> dict[int, dict]:
  return repo.get_feed_stats()


_JA_KANA = re.compile(r"[\u3040-\u309F\u30A0-\u30FF]")


def _detect_feed_lang(parsed: feedparser.FeedParserDict, url: str | None = None) -> str:
  """Detect language from feed metadata, article titles, and URL domain."""
  feed_lang = (parsed.feed or {}).get("language", "")
  if feed_lang:
    lang = feed_lang.split("-")[0].lower()
    if lang == "ja":
      return "ja"

  for entry in parsed.entries[:5]:
    title = entry.get("title", "")
    if title and _JA_KANA.search(title):
      return "ja"

  if url:
    from urllib.parse import urlparse
    host = urlparse(url).hostname or ""
    if host.endswith(".jp"):
      return "ja"

  return "en"


@dataclass
class ProbeResult:
  title: str | None = None
  lang: str | None = None
  site_url: str | None = None


async def _probe_feed(url: str) -> ProbeResult:
  """Fetch a feed URL and extract its title, language, and site URL."""
  try:
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
      resp = await client.get(url)
      resp.raise_for_status()
    parsed = feedparser.parse(resp.text)
    feed_meta = parsed.feed or {}
    title = feed_meta.get("title", "")
    lang = _detect_feed_lang(parsed, url)
    site_url = feed_meta.get("link", "")
    return ProbeResult(
      title=title.strip() or None,
      lang=lang,
      site_url=site_url.strip() or None,
    )
  except Exception:
    pass
  return ProbeResult()


async def _fetch_single_feed(feed: FeedRow) -> None:
  """Fetch articles from a single feed and persist to DB."""
  try:
    feed_cfg = feed.to_feed_cfg()
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
      articles, _ = await _fetch_rss(client, feed_cfg)
    if articles:
      repo.upsert_articles(articles, {feed.name: feed.id})
    if not feed.site_url and feed.url:
      probe = await _probe_feed(feed.url)
      if probe.site_url:
        repo.update_feed_site_url(feed.id, probe.site_url)
    repo.update_feed_fetch_status(feed.id, success=True)
  except Exception as e:
    import log
    log.warn(f"Failed to fetch feed '{feed.name}': {e}")
    repo.update_feed_fetch_status(feed.id, success=False, error=str(e))


@app.post("/api/feeds", status_code=201)
async def create_feed(body: FeedCreate) -> FeedRow:
  probe = await _probe_feed(body.url)
  if not body.name.strip():
    body.name = probe.title or body.url
  if not body.lang:
    body.lang = probe.lang or "en"
  feed = FeedRow(**body.model_dump(), site_url=probe.site_url)
  feed_id = repo.add_feed(feed)
  feeds = repo.list_feeds()
  created = next((f for f in feeds if f.id == feed_id), None)
  if created is None:
    raise HTTPException(404, "Feed not found after creation")
  asyncio.create_task(_fetch_single_feed(created))
  return created


@app.delete("/api/feeds", status_code=204)
def delete_all_feeds() -> None:
  repo.delete_all_feeds()


@app.post("/api/feeds/{feed_id}/fetch", status_code=202)
async def fetch_feed(feed_id: int) -> dict[str, str]:
  feed = next((f for f in repo.list_feeds() if f.id == feed_id), None)
  if feed is None:
    raise HTTPException(404, "Feed not found")
  asyncio.create_task(_fetch_single_feed(feed))
  return {"message": "Fetch started"}


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


class FeedLangUpdate(BaseModel):
  lang: str


@app.patch("/api/feeds/{feed_id}/lang")
def update_feed_lang(feed_id: int, body: FeedLangUpdate) -> FeedRow:
  repo.update_feed_lang(feed_id, body.lang)
  updated = next((f for f in repo.list_feeds() if f.id == feed_id), None)
  if updated is None:
    raise HTTPException(404, "Feed not found")
  return updated


class FeedRename(BaseModel):
  name: str


@app.patch("/api/feeds/{feed_id}/name")
def rename_feed(feed_id: int, body: FeedRename) -> FeedRow:
  repo.rename_feed(feed_id, body.name)
  updated = next((f for f in repo.list_feeds() if f.id == feed_id), None)
  if updated is None:
    raise HTTPException(404, "Feed not found")
  return updated


@app.patch("/api/feeds/{feed_id}/folder")
def move_feed_to_folder(feed_id: int, body: FeedMove) -> FeedRow:
  repo.move_feed_to_folder(feed_id, body.folder_id)
  updated = next((f for f in repo.list_feeds() if f.id == feed_id), None)
  if updated is None:
    raise HTTPException(404, "Feed not found")
  return updated


# -- NG words --


class NgWordCreate(BaseModel):
  pattern: str
  target: str = "title"


@app.get("/api/ng-words")
def get_ng_words() -> list[NgWordRow]:
  return repo.list_ng_words()


@app.post("/api/ng-words", status_code=201)
def create_ng_word(body: NgWordCreate) -> NgWordRow:
  ng_id = repo.add_ng_word(body.pattern, body.target)
  words = repo.list_ng_words()
  for w in words:
    if w.id == ng_id:
      return w
  raise HTTPException(404, "NG word not found after creation")


@app.delete("/api/ng-words/{ng_word_id}", status_code=204)
def delete_ng_word(ng_word_id: int) -> None:
  repo.delete_ng_word(ng_word_id)


# -- OPML --


@app.get("/api/opml/export")
def opml_export() -> Response:
  from opml import export_opml
  folders = repo.list_folders()
  feeds = repo.list_feeds()
  xml = export_opml(folders, feeds)
  return Response(
    content=xml,
    media_type="text/xml",
    headers={"Content-Disposition": 'attachment; filename="subscriptions.opml"'},
  )


@app.post("/api/opml/import")
async def opml_import(file: UploadFile) -> dict[str, int]:
  from opml import parse_opml
  content = (await file.read()).decode("utf-8")
  imported_folders, uncategorized = parse_opml(content)

  existing_urls = {f.url for f in repo.list_feeds() if f.url}
  folders_created = 0
  feeds_created = 0
  feeds_skipped = 0

  def _add_feed(feed_data, folder_id: int | None) -> None:
    nonlocal feeds_created, feeds_skipped
    if feed_data.url in existing_urls:
      feeds_skipped += 1
      return
    row = FeedRow(
      name=feed_data.name,
      url=feed_data.url,
      site_url=feed_data.site_url,
      lang=feed_data.lang,
      summarize=feed_data.summarize,
      folder_id=folder_id,
    )
    repo.add_feed(row)
    existing_urls.add(feed_data.url)
    feeds_created += 1

  existing_folders = {f.name: f.id for f in repo.list_folders()}
  for imp_folder in imported_folders:
    if imp_folder.name in existing_folders:
      folder_id = existing_folders[imp_folder.name]
    else:
      folder_id = repo.create_folder(imp_folder.name)
      existing_folders[imp_folder.name] = folder_id
      folders_created += 1
    for feed_data in imp_folder.feeds:
      _add_feed(feed_data, folder_id)

  for feed_data in uncategorized:
    _add_feed(feed_data, None)

  return {
    "folders_created": folders_created,
    "feeds_created": feeds_created,
    "feeds_skipped": feeds_skipped,
  }


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
    file = _frontend_dist / path
    if path and file.is_file():
      return FileResponse(str(file))
    return FileResponse(str(_frontend_dist / "index.html"))
