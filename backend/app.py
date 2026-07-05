"""FastAPI application — JSON API + SPA fallback."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import xml.etree.ElementTree as _ET
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import AsyncIterator, Coroutine, cast

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import cache
import db
import lang
import log
import repo
from feeds import _fetch_rss, extract_feed_candidates, probe_feed_body, read_file_url  # type: ignore[import-untyped]
from fetcher import STALE_CACHE_DIAGNOSTICS, refresh_all, summarize_pending
from url_cleaner import clean_url
from opml import ImportFeed, ImportFolder, export_opml, parse_opml
from scraper import fetch_web_page, validate_extraction_rules  # type: ignore[import-untyped]
from selector_inference import infer_and_validate
from schemas import (
  DEFAULT_USER_AGENT,
  AppConfig,
  ArticleRow,
  FeedHealth,
  FeedRow,
  FeedStats,
  FilterRow,
  FolderRow,
  StatusResponse,
)

# -- Globals for background refresh --
_refresh_lock = asyncio.Lock()
_refresh_task: asyncio.Task[None] | None = None
_summarize_lock = asyncio.Lock()
_background_tasks: set[asyncio.Task[object]] = set()
# Limit concurrent LLM selector inference so button mashing cannot flood Ollama.
_infer_semaphore = asyncio.Semaphore(2)
# Cap the raw HTML download for inference before preprocessing/truncation.
_INFER_MAX_HTML_FETCH_BYTES = 5 * 1024 * 1024


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


# LLM reachability probe. Cached because /api/status is polled as often as every 2s
# while a fetch is running — we don't need (or want) to hammer Ollama that hard.
# Timeout is deliberately generous and we retry once on failure: Docker Desktop's
# vmnet routing to host.docker.internal on macOS is flaky (cold-route ~1-2s,
# occasional transient timeouts even on warm routes). Without the retry a single
# vmnet hiccup would falsely mark Ollama offline for 20s.
_LLM_PROBE_TTL = 20.0
_LLM_PROBE_TIMEOUT = 5.0
_LLM_PROBE_ATTEMPTS = 3
_llm_probe_cache: dict[str, tuple[float, bool, str | None]] = {}


def _probe_llm_once(base_url: str) -> tuple[bool, str | None]:
  try:
    with httpx.Client(timeout=_LLM_PROBE_TIMEOUT) as client:
      resp = client.get(f"{base_url.rstrip('/')}/api/tags")
      resp.raise_for_status()
    return True, None
  except Exception as e:
    return False, f"{type(e).__name__}: {e}".strip()


def _probe_llm(base_url: str) -> tuple[bool, str | None]:
  """Return (available, error_message) for the given Ollama endpoint."""
  now = time.monotonic()
  cached = _llm_probe_cache.get(base_url)
  if cached is not None and now < cached[0]:
    return cached[1], cached[2]
  ok, err = False, None
  for _ in range(_LLM_PROBE_ATTEMPTS):
    ok, err = _probe_llm_once(base_url)
    if ok:
      break
  _llm_probe_cache[base_url] = (now + _LLM_PROBE_TTL, ok, err)
  return ok, err


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
  config = AppConfig.load()
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


app = FastAPI(title="Stingray", lifespan=lifespan)


# -- Articles --


@app.get("/api/articles")
def get_articles(
  request: Request,
  feed_id: int | None = Query(None),
  unread: bool = Query(False),
  since_days: int | None = Query(None, ge=1, le=3650),
  limit: int = Query(10000, ge=1, le=10000),
) -> list[ArticleRow]:
  config: AppConfig = request.app.state.config  # type: ignore[attr-defined]
  return repo.list_articles(
    feed_id=feed_id, unread=unread, since_days=since_days, limit=limit,
    order=config.article_order,
  )


class ArticleUrls(BaseModel):
  urls: list[str]


@app.post("/api/articles/read", status_code=204)
def mark_articles_read(body: ArticleUrls) -> None:
  repo.mark_read(body.urls)


@app.post("/api/articles/unread", status_code=204)
def mark_articles_unread(body: ArticleUrls) -> None:
  repo.mark_unread(body.urls)


@app.post("/api/articles/read-all")
def mark_all_articles_read(
  feed_id: int | None = Query(None),
  older_than_hours: int | None = Query(None, ge=1),
) -> dict[str, int]:
  count = repo.mark_all_read(feed_id, older_than_hours)
  return {"marked": count}


@app.post("/api/articles/unread-all")
def mark_all_articles_unread(
  feed_id: int | None = Query(None),
) -> dict[str, int]:
  count = repo.mark_all_unread(feed_id)
  return {"unmarked": count}


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
  feed_candidates: list[dict[str, str]] = field(default_factory=list["dict[str, str]"])


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


async def _probe_feed(
  url: str, native_lang: str = "ja", user_agent: str = DEFAULT_USER_AGENT
) -> ProbeResult:
  """Fetch a feed URL and extract its title, translate flag, and site URL.

  If the URL is not a valid RSS/Atom feed, detect it as a web page.
  """
  try:
    if url.startswith("file://"):
      # Debug-only: local file is always treated as RSS/Atom, skip HTML detection.
      body = read_file_url(url)
    else:
      async with httpx.AsyncClient(
        timeout=15, follow_redirects=True,
        headers={"User-Agent": user_agent},
      ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
      body = resp.text
      content_type = resp.headers.get("content-type", "")

      # Determine format before parsing
      if _is_html(content_type, body):
        title_match = re.search(r"<title[^>]*>(.*?)</title>", body, re.IGNORECASE | re.DOTALL)
        title = title_match.group(1).strip() if title_match else None
        # Resolve candidates against the response URL so redirects don't confuse relative hrefs.
        final_url = str(resp.url) or url
        candidates: list[dict[str, str]] = extract_feed_candidates(body, final_url)
        return ProbeResult(
          title=title,
          translate=False,
          site_url=url,
          is_web_page=True,
          html=body,
          feed_candidates=candidates,
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


def _manual_success_health(source_tag: str | None) -> tuple[FeedHealth, str | None]:
  """Classify a successful manual fetch as degraded (stale cache) or ok.

  Mirrors the scheduled path's stale-cache handling so a manual single-feed
  fetch that only served a cached copy surfaces as "degraded" with the same
  diagnostic, rather than silently looking healthy.
  """
  diagnostic = STALE_CACHE_DIAGNOSTICS.get(source_tag or "")
  if diagnostic is not None:
    return "degraded", diagnostic
  return "ok", None


async def _fetch_single_feed(feed: FeedRow, config: AppConfig) -> None:
  """Fetch articles from a single feed and persist to DB."""
  start = time.perf_counter()
  try:
    clean_url_fn = clean_url if config.url_cleanup.enabled else None
    feed_cfg = feed.to_feed_cfg()
    kind = "web" if feed.extraction_rules else "rss"
    log.step(f"Fetching feed [{kind}]: {feed.name} ({feed.url})")
    source_tag: str | None = None
    async with httpx.AsyncClient(
      timeout=30, follow_redirects=True,
      headers={"User-Agent": config.user_agent},
    ) as client:
      if feed.extraction_rules and feed.extraction_rules != "{}":
        articles, was_cached = await fetch_web_page(
          client, feed_cfg, clean_url_fn=clean_url_fn,
        )
      elif feed.extraction_rules is not None:
        log.warn(f"  Skipping '{feed.name}': extraction rules not configured yet.")
        repo.update_feed_fetch_status(feed.id, health="degraded", error="extraction rules not configured")
        return
      else:
        articles, was_cached, source_tag = await _fetch_rss(
          client, feed_cfg, clean_url_fn=clean_url_fn,
        )
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    source = "cache" if was_cached else "fresh"
    log.info(f"  [{source}] {len(articles)} items fetched in {elapsed_ms}ms.")
    if articles:
      new_count = repo.upsert_articles(articles, {feed.name: feed.id})
      log.info(f"  Saved: {new_count} new / {len(articles)} total (existing skipped).")
    else:
      log.info("  No articles returned.")
    if not feed.site_url and feed.url:
      probe = await _probe_feed(feed.url, user_agent=config.user_agent)
      if probe.site_url:
        repo.update_feed_site_url(feed.id, probe.site_url)
        log.info(f"  Updated site_url: {probe.site_url}")
    health, error = _manual_success_health(source_tag)
    repo.update_feed_fetch_status(feed.id, health=health, error=error)
    log.success(f"  Done: {feed.name}")
  except Exception as e:
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    log.error(f"  Failed [{feed.name}] after {elapsed_ms}ms: {type(e).__name__}: {e}")
    repo.update_feed_fetch_status(feed.id, health="failing", error=str(e))


@app.post("/api/feeds", status_code=201)
async def create_feed(body: FeedCreate, request: Request) -> FeedRow:
  config: AppConfig = request.app.state.config  # type: ignore[attr-defined]
  log.step(f"Probing feed: {body.url}")
  probe = await _probe_feed(
    body.url, native_lang=config.native_lang, user_agent=config.user_agent
  )
  if not body.name.strip():
    body.name = probe.title or body.url
  if not body.translate:
    body.translate = probe.translate
  # Foreign feeds detected by the probe also get summarize enabled so the body
  # is rendered in the native language right after adding — translate alone only
  # translates titles for long articles. Native feeds keep summarize opt-in.
  if probe.translate:
    body.summarize = True

  extraction_rules = None
  if probe.is_web_page:
    # If the page advertises feeds via <link rel="alternate">, hand them back
    # to the client instead of silently creating a web-scrape placeholder —
    # the user almost certainly wants to subscribe to the real feed.
    if probe.feed_candidates:
      log.info(f"  HTML page with {len(probe.feed_candidates)} feed candidate(s). Returning for user selection.")
      raise HTTPException(
        422,
        detail={
          "message": "This URL is an HTML page. Pick a feed below.",
          "candidates": probe.feed_candidates,
          "page_title": probe.title,
        },
      )
    extraction_rules = "{}"
    log.info("  Web page detected with no feed candidates. Extraction rules can be set in feed settings.")
  else:
    if not probe.title and not probe.site_url:
      raise HTTPException(422, "Could not parse this URL. Check the URL and server logs for details.")
    log.info(f"  RSS feed: {probe.title or '(no title)'}")

  feed = FeedRow(**body.model_dump(), site_url=probe.site_url, extraction_rules=extraction_rules)
  created = repo.add_feed(feed)
  log.info(f"  Feed created: id={created.id}, name={created.name}")
  # Web-scrape feeds are created with empty rules ("{}"); fetching now would
  # just fail (no "item" selector) and record an error. Defer the first fetch
  # until rules are inferred and saved. RSS/Atom feeds fetch immediately.
  if extraction_rules is None:
    _spawn_background(_fetch_single_feed(created, config))
  return created


@app.delete("/api/feeds", status_code=204)
async def delete_all_data() -> None:
  # Cancel any in-flight refresh first. Otherwise it would keep processing
  # the pre-delete feed list (wasted work, potential FK errors on upsert),
  # and its refresh_jobs row could stay 'running' if it crashes before
  # finish_refresh_job — leaving the UI stuck at "Fetching...".
  global _refresh_task
  async with _refresh_lock:
    task = _refresh_task
    if task is not None and not task.done():
      task.cancel()
      try:
        await task
      except (asyncio.CancelledError, Exception):
        pass
    _refresh_task = None
  repo.delete_all_data()
  purged = cache.purge_feed_cache()
  if purged:
    log.step(f"Purged {purged} cached feed bodies.")


@app.post("/api/feeds/{feed_id}/fetch", status_code=202)
async def fetch_feed(feed_id: int, request: Request) -> dict[str, str]:
  feed = repo.get_feed_by_id(feed_id)
  if feed is None:
    raise HTTPException(404, "Feed not found")
  config: AppConfig = request.app.state.config  # type: ignore[attr-defined]
  _spawn_background(_fetch_single_feed(feed, config))
  return {"message": "Fetch started"}


@app.delete("/api/feeds/{feed_id}", status_code=204)
def delete_feed(feed_id: int) -> None:
  repo.delete_feed(feed_id)


def _get_feed_or_404(feed_id: int) -> FeedRow:
  """Fetch a feed by id or raise 404. Used after every mutation handler so
  the response body matches the post-update state."""
  feed = repo.get_feed_by_id(feed_id)
  if feed is None:
    raise HTTPException(404, "Feed not found")
  return feed


@app.post("/api/feeds/{feed_id}/toggle")
def toggle_feed(feed_id: int) -> FeedRow:
  repo.toggle_feed(feed_id)
  return _get_feed_or_404(feed_id)


@app.post("/api/feeds/{feed_id}/summarize")
def toggle_summarize(feed_id: int) -> FeedRow:
  repo.toggle_summarize(feed_id)
  return _get_feed_or_404(feed_id)


@app.patch("/api/feeds/{feed_id}/rules")
async def update_extraction_rules(feed_id: int, request: Request) -> FeedRow:
  feed = _get_feed_or_404(feed_id)
  if feed.extraction_rules is None:
    raise HTTPException(400, "Not a web page feed")
  rules_raw: object = await request.json()
  try:
    rules = validate_extraction_rules(rules_raw)
  except ValueError as e:
    raise HTTPException(422, str(e))
  repo.update_feed_extraction_rules(feed_id, json.dumps(rules))
  return _get_feed_or_404(feed_id)


class SampleArticle(BaseModel):
  title: str
  url: str
  published: datetime | None = None


class InferRulesResponse(BaseModel):
  rules: dict[str, str]
  sample_articles: list[SampleArticle]
  attempts: int
  status: str


@app.post("/api/feeds/{feed_id}/rules/infer")
async def infer_feed_rules(feed_id: int, request: Request) -> InferRulesResponse:
  """Infer CSS extraction rules for a web-scrape feed via the LLM. Validates the
  rules against the live page and returns a preview — never persists them."""
  feed = _get_feed_or_404(feed_id)
  if feed.extraction_rules is None:
    raise HTTPException(400, "Not a web page feed")
  if not feed.url:
    raise HTTPException(400, "Feed has no URL")
  config: AppConfig = request.app.state.config  # type: ignore[attr-defined]
  if not config.ollama.enabled:
    raise HTTPException(503, "LLM is disabled (ollama.enabled=false)")
  ollama_url = os.environ.get("OLLAMA_BASE_URL") or config.ollama.base_url
  llm_ok, llm_err = _probe_llm(ollama_url)
  if not llm_ok:
    raise HTTPException(503, f"LLM is unavailable: {llm_err}")

  # Fetch the page, capping the download and using the final (post-redirect)
  # URL as the base for resolving relative article links.
  try:
    async with httpx.AsyncClient(
      follow_redirects=True, timeout=30,
      headers={"User-Agent": config.user_agent},
    ) as client:
      resp = await client.get(feed.url)
      resp.raise_for_status()
      raw = resp.content[:_INFER_MAX_HTML_FETCH_BYTES]
      html = raw.decode(resp.encoding or "utf-8", errors="ignore")
      page_url = str(resp.url)
  except httpx.HTTPError as e:
    raise HTTPException(502, f"Failed to fetch page: {e}")

  clean_url_fn = clean_url if config.url_cleanup.enabled else None
  async with _infer_semaphore:
    async with httpx.AsyncClient(
      base_url=ollama_url, timeout=config.ollama.timeout,
    ) as ollama_client:
      result = await infer_and_validate(
        ollama_client,
        config.ollama.model,
        html,
        page_url=page_url,
        source=feed.name,
        max_html_bytes=config.selector_inference.max_html_bytes,
        max_attempts=config.selector_inference.max_attempts,
        max_items=config.max_items_per_feed,
        min_articles=config.selector_inference.min_articles,
        num_ctx=config.selector_inference.num_ctx,
        clean_url_fn=clean_url_fn,
      )

  return InferRulesResponse(
    rules=result.rules,
    sample_articles=[
      SampleArticle(title=a.title, url=a.url, published=a.published)
      for a in result.sample_articles
    ],
    attempts=result.attempts,
    status=result.status,
  )


class FeedTranslateUpdate(BaseModel):
  translate: bool


@app.patch("/api/feeds/{feed_id}/translate")
def update_feed_translate(feed_id: int, body: FeedTranslateUpdate) -> FeedRow:
  repo.update_feed_translate(feed_id, body.translate)
  return _get_feed_or_404(feed_id)


class FeedRename(BaseModel):
  name: str


@app.patch("/api/feeds/{feed_id}/name")
def rename_feed(feed_id: int, body: FeedRename) -> FeedRow:
  repo.rename_feed(feed_id, body.name)
  return _get_feed_or_404(feed_id)


class FeedSiteUrlUpdate(BaseModel):
  site_url: str | None = None


@app.patch("/api/feeds/{feed_id}/site_url")
def update_feed_site_url(feed_id: int, body: FeedSiteUrlUpdate) -> FeedRow:
  site_url = body.site_url.strip() if body.site_url else None
  repo.update_feed_site_url(feed_id, site_url or None)
  return _get_feed_or_404(feed_id)


@app.patch("/api/feeds/{feed_id}/folder")
def move_feed_to_folder(feed_id: int, body: FeedMove) -> FeedRow:
  repo.move_feed_to_folder(feed_id, body.folder_id)
  return _get_feed_or_404(feed_id)


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
  filename = f"stingray_subscriptions_{date.today().strftime('%Y%m%d')}.opml"
  return Response(
    content=xml,
    media_type="text/xml",
    headers={"Content-Disposition": f'attachment; filename="{filename}"'},
  )


def _persist_opml_feeds(
  imported_folders: list[ImportFolder],
  uncategorized: list[ImportFeed],
) -> tuple[int, int, int]:
  """Create any missing folders/feeds from parsed OPML.

  Returns (folders_created, feeds_created, feeds_skipped). Feeds whose URL
  already exists in the DB are skipped rather than re-added.
  """
  existing_urls = {f.url for f in repo.list_feeds() if f.url}
  existing_folders = {f.name: f.id for f in repo.list_folders()}
  folders_created = 0
  feeds_created = 0
  feeds_skipped = 0

  def _add_feed(feed_data: ImportFeed, folder_id: int | None) -> None:
    nonlocal feeds_created, feeds_skipped
    if feed_data.url in existing_urls:
      feeds_skipped += 1
      return
    # Append in file order so an imported OPML keeps its original ordering,
    # rather than reversing it (the interactive add-at-top default).
    repo.add_feed(
      FeedRow(
        name=feed_data.name,
        url=feed_data.url,
        site_url=feed_data.site_url,
        translate=feed_data.translate,
        summarize=feed_data.summarize,
        folder_id=folder_id,
        extraction_rules=feed_data.extraction_rules,
      ),
      at_top=False,
    )
    existing_urls.add(feed_data.url)
    feeds_created += 1

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

  return folders_created, feeds_created, feeds_skipped


async def _maybe_trigger_opml_refresh(request: Request, feeds_created: int) -> None:
  """Kick off a refresh if OPML import added new feeds, without blocking the
  response. The refresh_jobs row is created synchronously so /api/status
  reports running=true as soon as this response reaches the client.
  """
  if feeds_created <= 0:
    return
  global _refresh_task
  async with _refresh_lock:
    if _refresh_task is not None and not _refresh_task.done():
      return
    config: AppConfig = request.app.state.config  # type: ignore[attr-defined]
    log.info(f"OPML import added {feeds_created} feeds, auto-triggering refresh.")
    job_id = repo.create_refresh_job("opml")
    _refresh_task = asyncio.create_task(
      _run_refresh(config, trigger="opml", job_id=job_id)
    )
    _background_tasks.add(_refresh_task)
    _refresh_task.add_done_callback(_log_task_exception)


@app.post("/api/opml/import")
async def opml_import(file: UploadFile, request: Request) -> dict[str, int]:
  config: AppConfig = request.app.state.config  # type: ignore[attr-defined]
  try:
    content = (await file.read()).decode("utf-8")
    imported_folders, uncategorized = parse_opml(content, native_lang=config.native_lang)
  except UnicodeDecodeError as e:
    raise HTTPException(400, f"Invalid file encoding: {e}")
  except _ET.ParseError as e:
    raise HTTPException(400, f"Invalid OPML XML: {e}")

  folders_created, feeds_created, feeds_skipped = _persist_opml_feeds(
    imported_folders, uncategorized,
  )
  await _maybe_trigger_opml_refresh(request, feeds_created)
  return {
    "folders_created": folders_created,
    "feeds_created": feeds_created,
    "feeds_skipped": feeds_skipped,
  }


# -- Refresh --


async def _run_refresh(
  config: AppConfig,
  *,
  trigger: str = "web",
  job_id: int | None = None,
  force: bool = False,
) -> None:
  global _refresh_task
  start = time.perf_counter()
  log.step(f"Refresh started (trigger={trigger}, force={force})")
  if _summarize_lock.locked():
    log.info("  Waiting for summarizer lock...")
  try:
    async with _summarize_lock:
      await refresh_all(config, source=trigger, job_id=job_id, force=force)
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
    # Create the refresh_jobs row synchronously so /api/status reports
    # running=true by the time this response reaches the client.
    job_id = repo.create_refresh_job("web")
    _refresh_task = asyncio.create_task(
      _run_refresh(config, trigger="web", job_id=job_id, force=True)
    )
    _background_tasks.add(_refresh_task)
    _refresh_task.add_done_callback(_log_task_exception)
  return JSONResponse({"message": "Refresh started"}, status_code=202)


@app.get("/api/status")
def get_status(request: Request) -> StatusResponse:
  config: AppConfig = request.app.state.config  # type: ignore[attr-defined]
  if config.ollama.enabled:
    # Env var wins so containers can override the config.yml default (which
    # usually points at localhost — not reachable from inside Docker).
    ollama_url = os.environ.get("OLLAMA_BASE_URL") or config.ollama.base_url
    llm_ok, llm_err = _probe_llm(ollama_url)
  else:
    # Disabled by config: no probe, no noisy offline badge on the frontend.
    llm_ok, llm_err = False, None
  llm_enabled = config.ollama.enabled
  job = repo.get_latest_refresh_job()
  if job is None:
    return StatusResponse(running=False, llm_enabled=llm_enabled, llm_available=llm_ok, llm_error=llm_err)
  return StatusResponse(
    running=job.status == "running",
    last_started_at=job.started_at,
    last_finished_at=job.finished_at,
    last_status=job.status,
    last_new_count=job.new_count,
    last_error=job.error,
    llm_enabled=llm_enabled,
    llm_available=llm_ok,
    llm_error=llm_err,
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
