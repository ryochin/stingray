"""FastAPI application — JSON API + SPA fallback."""

from __future__ import annotations

import asyncio
import json
import re
import time
import xml.etree.ElementTree as _ET
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Coroutine, cast

import httpx
import yaml
from fastapi import FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
import lang
import log
import repo
from feeds import _fetch_rss, probe_feed_body  # type: ignore[import-untyped]
from fetcher import refresh_all, summarize_pending
from opml import ImportFeed, export_opml, parse_opml
from scraper import fetch_web_page  # type: ignore[import-untyped]
from schemas import AppConfig, ArticleRow, FeedRow, FeedStats, FilterRow, FolderRow, StatusResponse

# -- Globals for background refresh --
_refresh_lock = asyncio.Lock()
_refresh_task: asyncio.Task[None] | None = None
_summarize_lock = asyncio.Lock()
_background_tasks: set[asyncio.Task[object]] = set()


def _log_task_exception(task: asyncio.Task[object]) -> None:
  _background_tasks.discard(task)
  if task.cancelled():
    return
  exc = task.exception()
  if exc is not None:
    log.warn(f"  Background task failed: {exc}")


def _spawn_background(coro: Coroutine[object, object, object]) -> asyncio.Task[object]:
  """Create a fire-and-forget task while holding a strong reference so the GC
  doesn't collect it before completion."""
  task = asyncio.create_task(coro)
  _background_tasks.add(task)
  task.add_done_callback(_log_task_exception)
  return task


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
    if _summarize_lock.locked():
      log.info("  Background summarizer skipped (another summarize in progress).")
    else:
      async with _summarize_lock:
        try:
          count = await summarize_pending(config)
          if count > 0:
            log.info(f"  Background summarizer processed {count} articles.")
        except Exception as e:
          log.warn(f"  Background summarizer error: {e}")
    await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
  config = _load_config()
  db.configure()
  db.init_schema()
  app.state.config = config  # type: ignore[attr-defined]
  task = asyncio.create_task(_background_summarizer(config))
  _background_tasks.add(task)
  task.add_done_callback(_background_tasks.discard)
  try:
    yield
  finally:
    task.cancel()
    db.close()


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
  return repo.create_folder(body.name)


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


class FeedReorder(BaseModel):
  feed_ids: list[int]


@app.put("/api/feeds/reorder", status_code=204)
def reorder_feeds(body: FeedReorder) -> None:
  repo.reorder_feeds(body.feed_ids)


@app.get("/api/feeds/stats")
def get_feed_stats() -> dict[int, FeedStats]:
  return repo.get_feed_stats()


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
    title, site_url, feed_lang, has_entries = probe_feed_body(body)
    if has_entries:
      detected_lang = feed_lang or lang.detect_lang_by_tld(site_url or url)
      return ProbeResult(
        title=title,
        translate=lang.should_translate(detected_lang, native_lang),
        site_url=site_url,
      )
  except Exception as e:
    log.warn(f"  Probe failed: {e}")
  return ProbeResult()


async def _fetch_single_feed(feed: FeedRow) -> None:
  """Fetch articles from a single feed and persist to DB."""
  start = time.perf_counter()
  try:
    feed_cfg = feed.to_feed_cfg()
    kind = "web" if feed.extraction_rules else "rss"
    log.step(f"Fetching feed [{kind}]: {feed.name} ({feed.url})")
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
      if feed.extraction_rules and feed.extraction_rules != "{}":
        articles, was_cached = await fetch_web_page(client, feed_cfg)
      elif feed.extraction_rules is not None:
        log.warn(f"  Skipping '{feed.name}': extraction rules not configured yet.")
        repo.update_feed_fetch_status(feed.id, success=False, error="extraction rules not configured")
        return
      else:
        articles, was_cached = await _fetch_rss(client, feed_cfg)
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    source = "cache" if was_cached else "fresh"
    log.info(f"  [{source}] {len(articles)} items fetched in {elapsed_ms}ms.")
    if articles:
      new_count = repo.upsert_articles(articles, {feed.name: feed.id})
      log.info(f"  Saved: {new_count} new / {len(articles)} total (existing skipped).")
    else:
      log.info("  No articles returned.")
    if not feed.site_url and feed.url:
      probe = await _probe_feed(feed.url)
      if probe.site_url:
        repo.update_feed_site_url(feed.id, probe.site_url)
        log.info(f"  Updated site_url: {probe.site_url}")
    repo.update_feed_fetch_status(feed.id, success=True)
    log.success(f"  Done: {feed.name}")
  except Exception as e:
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    log.error(f"  Failed [{feed.name}] after {elapsed_ms}ms: {type(e).__name__}: {e}")
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
  created = repo.add_feed(feed)
  log.info(f"  Feed created: id={created.id}, name={created.name}")
  _spawn_background(_fetch_single_feed(created))
  return created


@app.delete("/api/feeds", status_code=204)
def delete_all_data() -> None:
  repo.delete_all_data()


@app.post("/api/feeds/{feed_id}/fetch", status_code=202)
async def fetch_feed(feed_id: int) -> dict[str, str]:
  feed = repo.get_feed_by_id(feed_id)
  if feed is None:
    raise HTTPException(404, "Feed not found")
  _spawn_background(_fetch_single_feed(feed))
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
  rules_raw: object = await request.json()
  if not isinstance(rules_raw, dict):
    raise HTTPException(422, "Must be a JSON object")
  rules = cast(dict[str, object], rules_raw)
  for key in ("item", "title", "link"):
    value = rules.get(key)
    if not value or not isinstance(value, str):
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
  return repo.add_filter(body.pattern, body.target)


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
    data: object = json.loads(content)
  except (UnicodeDecodeError, json.JSONDecodeError) as exc:
    raise HTTPException(400, f"Invalid JSON: {exc}")
  if not isinstance(data, list):
    raise HTTPException(400, "Expected a JSON array")
  existing = {(f.pattern, f.target) for f in repo.list_filters()}
  created = 0
  skipped = 0
  for raw_item in cast(list[object], data):
    if not isinstance(raw_item, dict) or "pattern" not in raw_item:
      continue
    item = cast(dict[str, object], raw_item)
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
    config = _load_config()
    imported_folders, uncategorized = parse_opml(content, native_lang=config.native_lang)
  except UnicodeDecodeError as e:
    raise HTTPException(400, f"Invalid file encoding: {e}")
  except _ET.ParseError as e:
    raise HTTPException(400, f"Invalid OPML XML: {e}")

  existing_urls = {f.url for f in repo.list_feeds() if f.url}
  folders_created = 0
  feeds_created = 0
  feeds_skipped = 0

  def _add_feed(feed_data: ImportFeed, folder_id: int | None) -> None:
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
      folder = repo.create_folder(imp_folder.name)
      folder_id = folder.id
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
        log.info(f"OPML import added {feeds_created} feeds, auto-triggering refresh.")
        _refresh_task = asyncio.create_task(_run_refresh(config, trigger="opml"))
        _background_tasks.add(_refresh_task)
        _refresh_task.add_done_callback(_log_task_exception)

  return {
    "folders_created": folders_created,
    "feeds_created": feeds_created,
    "feeds_skipped": feeds_skipped,
  }


# -- Refresh --


async def _run_refresh(config: AppConfig, *, trigger: str = "web") -> None:
  global _refresh_task
  start = time.perf_counter()
  log.step(f"Refresh started (trigger={trigger})")
  if _summarize_lock.locked():
    log.info("  Waiting for summarizer lock...")
  try:
    async with _summarize_lock:
      await refresh_all(config, source=trigger)
    elapsed = time.perf_counter() - start
    log.success(f"Refresh completed in {elapsed:.1f}s (trigger={trigger})")
  except Exception as e:
    elapsed = time.perf_counter() - start
    log.error(f"Refresh failed after {elapsed:.1f}s (trigger={trigger}): {type(e).__name__}: {e}")
    raise
  finally:
    _refresh_task = None


@app.post("/api/refresh")
async def trigger_refresh(request: Request) -> JSONResponse:
  global _refresh_task
  async with _refresh_lock:
    if _refresh_task is not None and not _refresh_task.done():
      log.info("Refresh requested but one is already in progress.")
      return JSONResponse({"message": "Refresh already in progress"}, status_code=409)
    config: AppConfig = request.app.state.config  # type: ignore[attr-defined]
    _refresh_task = asyncio.create_task(_run_refresh(config, trigger="web"))
    _background_tasks.add(_refresh_task)
    _refresh_task.add_done_callback(_log_task_exception)
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
  try:
    with db.connection() as conn:
      conn.execute("SELECT 1").fetchone()
  except Exception as e:
    raise HTTPException(status_code=503, detail=f"db: {type(e).__name__}: {e}")
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
