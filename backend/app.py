"""FastAPI application — JSON API + SPA fallback."""

from __future__ import annotations

import asyncio
import json
import re
import xml.etree.ElementTree as _ET
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import urlparse

import feedparser
import httpx
import yaml
from fastapi import FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
import log
import repo
from feeds import _fetch_rss
from fetcher import refresh_all, summarize_pending
from models import JA_KANA
from opml import export_opml, parse_opml
from scraper import fetch_web_page
from schemas import AppConfig, ArticleRow, FeedRow, FeedStats, FilterRow, FolderRow, StatusResponse

# -- Globals for background refresh --
_refresh_lock = asyncio.Lock()
_refresh_task: asyncio.Task[None] | None = None
_summarize_lock = asyncio.Lock()


def _log_task_exception(task: asyncio.Task[object]) -> None:
  if task.cancelled():
    return
  exc = task.exception()
  if exc is not None:
    log.warn(f"  Background task failed: {exc}")


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
  await asyncio.sleep(10)  # initial delay
  while True:
    # Non-blocking lock acquisition: skip this tick if a refresh (or another
    # summarizer run) already holds the lock. Avoids the TOCTOU race between
    # `locked()` and `async with`.
    try:
      await asyncio.wait_for(_summarize_lock.acquire(), timeout=0)
    except asyncio.TimeoutError:
      log.info("  Background summarizer skipped (refresh in progress).")
    else:
      try:
        count = await summarize_pending(config)
        if count > 0:
          log.info(f"  Background summarizer processed {count} articles.")
      except Exception as e:
        log.warn(f"  Background summarizer error: {e}")
      finally:
        _summarize_lock.release()
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
  translate: bool = False
  summarize: bool = False
  folder_id: int | None = None


@app.get("/api/feeds")
def get_feeds() -> list[FeedRow]:
  return repo.list_feeds()


@app.get("/api/feeds/stats")
def get_feed_stats() -> dict[int, FeedStats]:
  return repo.get_feed_stats()


def _detect_feed_lang(parsed: feedparser.FeedParserDict, url: str | None = None) -> str | None:
  """Detect language from feed metadata, article titles, and URL domain.

  Returns a 2-letter language code or None if detection fails.
  """
  feed_lang = (parsed.feed or {}).get("language", "")
  if feed_lang:
    lang = feed_lang.split("-")[0].lower()
    if len(lang) == 2:
      return lang

  for entry in parsed.entries[:5]:
    title = entry.get("title", "")
    if title and JA_KANA.search(title):
      return "ja"

  if url:
    host = urlparse(url).hostname or ""
    if host.endswith(".jp"):
      return "ja"

  return None


@dataclass
class ProbeResult:
  title: str | None = None
  translate: bool = False
  site_url: str | None = None
  is_web_page: bool = False
  html: str = ""


def _is_html(content_type: str, body: str) -> bool:
  """Determine if the response is an HTML page based on content-type and body heuristics."""
  ct = content_type.lower().split(";")[0].strip()
  if ct == "text/html" or ct == "application/xhtml+xml":
    return True
  # Check for feed content types
  if ct in ("application/rss+xml", "application/atom+xml", "text/xml", "application/xml"):
    return False
  # Heuristic: inspect the first non-whitespace characters
  prefix = body.lstrip()[:500].lower()
  if prefix.startswith("<!doctype html"):
    return True
  if prefix.startswith("<html"):
    return True
  if prefix.startswith("<?xml"):
    # XML declaration — check what follows for RSS/Atom root elements
    return False
  if any(prefix.startswith(tag) for tag in ("<rss", "<feed", "<rdf:rdf")):
    return False
  # Contains <html> tag anywhere in the prefix
  if "<html" in prefix:
    return True
  return False


async def _probe_feed(url: str, native_lang: str = "ja") -> ProbeResult:
  """Fetch a feed URL and extract its title, translate flag, and site URL.

  If the URL is not a valid RSS/Atom feed, detect it as a web page.
  """
  try:
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
      resp = await client.get(url)
      resp.raise_for_status()
    body = resp.text
    content_type = resp.headers.get("content-type", "")

    # Determine format before parsing
    if _is_html(content_type, body):
      title_match = re.search(r"<title[^>]*>(.*?)</title>", body, re.IGNORECASE | re.DOTALL)
      title = title_match.group(1).strip() if title_match else None
      return ProbeResult(
        title=title,
        translate=False,
        site_url=url,
        is_web_page=True,
        html=body,
      )

    # Try RSS/Atom parsing
    parsed = feedparser.parse(body)
    if parsed.entries:
      feed_meta = parsed.feed or {}
      title = feed_meta.get("title", "")
      detected_lang = _detect_feed_lang(parsed, url)
      site_url = feed_meta.get("link", "")
      return ProbeResult(
        title=title.strip() or None,
        translate=detected_lang is None or detected_lang != native_lang,
        site_url=site_url.strip() or None,
      )
  except Exception as e:
    log.warn(f"  Probe failed: {e}")
  return ProbeResult()


async def _fetch_single_feed(feed: FeedRow) -> None:
  """Fetch articles from a single feed and persist to DB."""
  try:
    feed_cfg = feed.to_feed_cfg()
    log.step(f"Fetching feed: {feed.name}")
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
      if feed.extraction_rules and feed.extraction_rules != "{}":
        articles, _ = await fetch_web_page(client, feed_cfg)
      elif feed.extraction_rules is not None:
        log.info("  Skipping: extraction rules not configured yet.")
        return
      else:
        articles, _ = await _fetch_rss(client, feed_cfg)
    log.info(f"  Got {len(articles)} articles.")
    if articles:
      repo.upsert_articles(articles, {feed.name: feed.id})
    if not feed.site_url and feed.url:
      probe = await _probe_feed(feed.url)
      if probe.site_url:
        repo.update_feed_site_url(feed.id, probe.site_url)
    repo.update_feed_fetch_status(feed.id, success=True)
  except Exception as e:
    log.warn(f"Failed to fetch feed '{feed.name}': {e}")
    repo.update_feed_fetch_status(feed.id, success=False, error=str(e))


@app.post("/api/feeds", status_code=201)
async def create_feed(body: FeedCreate) -> FeedRow:
  config = _load_config()
  log.step(f"Probing feed: {body.url}")
  probe = await _probe_feed(body.url, native_lang=config.native_lang)
  if not body.name.strip():
    body.name = probe.title or body.url
  if not body.translate:
    body.translate = probe.translate

  extraction_rules = None
  if probe.is_web_page:
    extraction_rules = "{}"
    log.info("  Web page detected. Extraction rules can be set in feed settings.")
  else:
    if not probe.title and not probe.site_url:
      raise HTTPException(422, "Could not parse this URL. Check the URL and server logs for details.")
    log.info(f"  RSS feed: {probe.title or '(no title)'}")

  feed = FeedRow(**body.model_dump(), site_url=probe.site_url, extraction_rules=extraction_rules)
  feed_id = repo.add_feed(feed)
  created = repo.get_feed_by_id(feed_id)
  if created is None:
    raise HTTPException(404, "Feed not found after creation")
  log.info(f"  Feed created: id={feed_id}, name={created.name}")
  asyncio.create_task(_fetch_single_feed(created)).add_done_callback(_log_task_exception)
  return created


@app.delete("/api/feeds", status_code=204)
def delete_all_data() -> None:
  repo.delete_all_data()


@app.post("/api/feeds/{feed_id}/fetch", status_code=202)
async def fetch_feed(feed_id: int) -> dict[str, str]:
  feed = repo.get_feed_by_id(feed_id)
  if feed is None:
    raise HTTPException(404, "Feed not found")
  asyncio.create_task(_fetch_single_feed(feed)).add_done_callback(_log_task_exception)
  return {"message": "Fetch started"}


@app.delete("/api/feeds/{feed_id}", status_code=204)
def delete_feed(feed_id: int) -> None:
  repo.delete_feed(feed_id)


@app.post("/api/feeds/{feed_id}/toggle")
def toggle_feed(feed_id: int) -> FeedRow:
  repo.toggle_feed(feed_id)
  updated = repo.get_feed_by_id(feed_id)
  if updated is None:
    raise HTTPException(404, "Feed not found")
  return updated


@app.post("/api/feeds/{feed_id}/summarize")
def toggle_summarize(feed_id: int) -> FeedRow:
  repo.toggle_summarize(feed_id)
  updated = repo.get_feed_by_id(feed_id)
  if updated is None:
    raise HTTPException(404, "Feed not found")
  return updated


@app.patch("/api/feeds/{feed_id}/rules")
async def update_extraction_rules(feed_id: int, request: Request) -> FeedRow:
  feed = repo.get_feed_by_id(feed_id)
  if feed is None:
    raise HTTPException(404, "Feed not found")
  if feed.extraction_rules is None:
    raise HTTPException(400, "Not a web page feed")
  rules = await request.json()
  if not isinstance(rules, dict):
    raise HTTPException(422, "Must be a JSON object")
  for key in ("item", "title", "link"):
    if not rules.get(key) or not isinstance(rules[key], str):
      raise HTTPException(422, f"Missing or empty required field: {key}")
  repo.update_feed_extraction_rules(feed_id, json.dumps(rules))
  updated = repo.get_feed_by_id(feed_id)
  if updated is None:
    raise HTTPException(404, "Feed not found")
  return updated


class FeedTranslateUpdate(BaseModel):
  translate: bool


@app.patch("/api/feeds/{feed_id}/translate")
def update_feed_translate(feed_id: int, body: FeedTranslateUpdate) -> FeedRow:
  repo.update_feed_translate(feed_id, body.translate)
  updated = repo.get_feed_by_id(feed_id)
  if updated is None:
    raise HTTPException(404, "Feed not found")
  return updated


class FeedRename(BaseModel):
  name: str


@app.patch("/api/feeds/{feed_id}/name")
def rename_feed(feed_id: int, body: FeedRename) -> FeedRow:
  repo.rename_feed(feed_id, body.name)
  updated = repo.get_feed_by_id(feed_id)
  if updated is None:
    raise HTTPException(404, "Feed not found")
  return updated


@app.patch("/api/feeds/{feed_id}/folder")
def move_feed_to_folder(feed_id: int, body: FeedMove) -> FeedRow:
  repo.move_feed_to_folder(feed_id, body.folder_id)
  updated = repo.get_feed_by_id(feed_id)
  if updated is None:
    raise HTTPException(404, "Feed not found")
  return updated


# -- Filters --


class FilterCreate(BaseModel):
  pattern: str
  target: str = "title"


@app.get("/api/filters")
def get_filters() -> list[FilterRow]:
  return repo.list_filters()


@app.post("/api/filters", status_code=201)
def create_filter(body: FilterCreate) -> FilterRow:
  filter_id = repo.add_filter(body.pattern, body.target)
  filters = repo.list_filters()
  for f in filters:
    if f.id == filter_id:
      return f
  raise HTTPException(404, "Filter not found after creation")


@app.delete("/api/filters/{filter_id}", status_code=204)
def delete_filter(filter_id: int) -> None:
  repo.delete_filter(filter_id)


@app.get("/api/filters/export")
def export_filters() -> Response:
  filters = repo.list_filters()
  data = [{"pattern": f.pattern, "target": f.target} for f in filters]
  return Response(
    content=json.dumps(data, ensure_ascii=False, indent=2),
    media_type="application/json",
    headers={"Content-Disposition": 'attachment; filename="filters.json"'},
  )


@app.post("/api/filters/import")
async def import_filters(file: UploadFile) -> dict[str, int]:
  try:
    content = (await file.read()).decode("utf-8")
    data = json.loads(content)
  except (UnicodeDecodeError, json.JSONDecodeError) as exc:
    raise HTTPException(400, f"Invalid JSON: {exc}")
  if not isinstance(data, list):
    raise HTTPException(400, "Expected a JSON array")
  existing = {(f.pattern, f.target) for f in repo.list_filters()}
  created = 0
  skipped = 0
  for item in data:
    if not isinstance(item, dict) or "pattern" not in item:
      continue
    pattern = str(item["pattern"]).strip()
    target = str(item.get("target", "title")).strip()
    if not pattern or target not in ("title", "both"):
      continue
    if (pattern, target) in existing:
      skipped += 1
      continue
    repo.add_filter(pattern, target)
    existing.add((pattern, target))
    created += 1
  return {"created": created, "skipped": skipped}


# -- OPML --


@app.get("/api/opml/export")
def opml_export() -> Response:
  folders = repo.list_folders()
  feeds = repo.list_feeds()
  xml = export_opml(folders, feeds)
  return Response(
    content=xml,
    media_type="text/xml",
    headers={"Content-Disposition": 'attachment; filename="subscriptions.opml"'},
  )


@app.post("/api/opml/import")
async def opml_import(file: UploadFile, request: Request) -> dict[str, int]:
  try:
    content = (await file.read()).decode("utf-8")
    imported_folders, uncategorized = parse_opml(content)
  except UnicodeDecodeError as e:
    raise HTTPException(400, f"Invalid file encoding: {e}")
  except _ET.ParseError as e:
    raise HTTPException(400, f"Invalid OPML XML: {e}")

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
      translate=feed_data.translate,
      summarize=feed_data.summarize,
      folder_id=folder_id,
      extraction_rules=feed_data.extraction_rules,
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

  # Auto-refresh if new feeds were added
  if feeds_created > 0:
    global _refresh_task
    async with _refresh_lock:
      if _refresh_task is None or _refresh_task.done():
        config: AppConfig = request.app.state.config  # type: ignore[attr-defined]
        _refresh_task = asyncio.create_task(_run_refresh(config))

  return {
    "folders_created": folders_created,
    "feeds_created": feeds_created,
    "feeds_skipped": feeds_skipped,
  }


# -- Refresh --


async def _run_refresh(config: AppConfig) -> None:
  global _refresh_task
  try:
    async with _summarize_lock:
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
    file = (_frontend_dist / path).resolve()
    if path and file.is_file() and str(file).startswith(str(_frontend_dist.resolve())):
      return FileResponse(str(file))
    return FileResponse(str(_frontend_dist / "index.html"))
