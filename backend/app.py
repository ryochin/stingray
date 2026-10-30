"""FastAPI application — JSON API + SPA fallback."""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
import xml.etree.ElementTree as _ET
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Literal, cast, get_args

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StringConstraints

import cache
import db
import lang
import log
import repo
from feeds import extract_feed_candidates, fetch_one_feed, probe_feed_body, read_file_url
from fetcher import classify_outcome, refresh_all, summarize_pending
from opml import ImportFeed, ImportFolder, export_opml, parse_opml
from origin_guard import OriginGuardMiddleware
from safe_http import FetchSettings, SafeClient
from schemas import (
  AppConfig,
  ArticleRow,
  FeedHealth,
  FeedRow,
  FeedStats,
  FilterRow,
  FolderRow,
  StatusResponse,
)
from scraper import validate_extraction_rules
from selector_inference import infer_and_validate
from url_cleaner import clean_url

# -- Globals for background refresh --
_refresh_lock = asyncio.Lock()
_refresh_task: asyncio.Task[None] | None = None
_summarize_lock = asyncio.Lock()
_opml_import_lock = asyncio.Lock()
_background_tasks: set[asyncio.Task[object]] = set()
# Limit concurrent LLM selector inference so button mashing cannot flood Ollama.
_infer_semaphore = asyncio.Semaphore(2)
# Cap the raw HTML download for inference before preprocessing/truncation.
_INFER_MAX_HTML_FETCH_BYTES = 5 * 1024 * 1024
# How long shutdown waits for cancelled background tasks. Kept well below
# Docker's default 10 s stop grace period, which also covers the rest of
# shutdown.
_SHUTDOWN_TIMEOUT_S = 5.0


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
# Guards _llm_probe_cache and _llm_probe_inflight. The probe itself deliberately
# runs outside the lock: holding it across an unresponsive Ollama would serialize
# every caller behind the very timeout we are trying to absorb.
_llm_probe_lock = threading.Lock()
# base_url -> event that the owning probe sets on completion. Callers that find
# an entry here must not start a probe of their own.
_llm_probe_inflight: dict[str, threading.Event] = {}
# Upper bound for sharing an in-flight probe, with a margin over the worst case
# the probe itself can take.
_LLM_PROBE_WAIT_TIMEOUT = _LLM_PROBE_TIMEOUT * _LLM_PROBE_ATTEMPTS + 1.0


def _probe_llm_once(base_url: str) -> tuple[bool, str | None]:
  try:
    with httpx.Client(timeout=_LLM_PROBE_TIMEOUT) as client:
      resp = client.get(f"{base_url.rstrip('/')}/api/tags")
      resp.raise_for_status()
    return True, None
  except Exception as e:
    return False, f"{type(e).__name__}: {e}".strip()


def _probe_llm(base_url: str) -> tuple[bool, str | None]:
  """Return (available, error_message) for the given Ollama endpoint.

  Blocking. Callers running on the event loop must go through asyncio.to_thread.

  Exactly one probe per endpoint runs at a time. This matters most when Ollama
  is unreachable: /api/status is polled every 2s during a fetch, so without
  de-duplication a 15s probe would stack up ~7 threads all waiting on the same
  dead endpoint. Callers that arrive while a probe is running take one of two
  paths:

    - a previous result exists -> return it even if expired. It may be staler
      than one TTL, but an availability flag lagging behind beats blocking.
    - nothing has ever been cached -> wait for the running probe and share its
      result, rather than starting a second one.
  """
  now = time.monotonic()
  with _llm_probe_lock:
    cached = _llm_probe_cache.get(base_url)
    if cached is not None and now < cached[0]:
      return cached[1], cached[2]
    running = _llm_probe_inflight.get(base_url)
    if running is not None and cached is not None:
      return cached[1], cached[2]
    owner = running is None
    if owner:
      running = threading.Event()
      _llm_probe_inflight[base_url] = running

  assert running is not None  # narrowing: set on both branches above
  if not owner:
    running.wait(timeout=_LLM_PROBE_WAIT_TIMEOUT)
    with _llm_probe_lock:
      shared = _llm_probe_cache.get(base_url)
    if shared is not None:
      return shared[1], shared[2]
    return False, "LLM probe did not complete"

  ok: bool = False
  err: str | None = None
  try:
    for _ in range(_LLM_PROBE_ATTEMPTS):
      ok, err = _probe_llm_once(base_url)
      if ok:
        break
  finally:
    # TTL runs from completion, not from entry: a probe that took 15s to fail
    # would otherwise be considered stale almost immediately.
    with _llm_probe_lock:
      _llm_probe_cache[base_url] = (time.monotonic() + _LLM_PROBE_TTL, ok, err)
      _llm_probe_inflight.pop(base_url, None)
    running.set()
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
  cache.configure(Path(config.cache_dir))
  app.state.config = config  # type: ignore[attr-defined]
  task = asyncio.create_task(_background_summarizer(config))
  _background_tasks.add(task)
  task.add_done_callback(_background_tasks.discard)
  try:
    yield
  finally:
    await _cancel_background_tasks(_SHUTDOWN_TIMEOUT_S)
    db.close()


async def _cancel_background_tasks(timeout: float) -> None:
  """Cancel the app's background tasks and wait for them before the pool closes.

  Their cleanup (e.g. closing a cancelled refresh's job row) still needs the
  DB. Best effort: worker threads behind to_thread are not stopped by
  cancellation, and tasks still pending after `timeout` are left behind. Only
  this wait is bounded; waiting for in-flight requests and closing the pool
  are not.
  """
  tasks = list(_background_tasks)
  if not tasks:
    return
  for task in tasks:
    task.cancel()
  _, pending = await asyncio.wait(tasks, timeout=timeout)
  if pending:
    log.warn(f"Shutdown: {len(pending)} background task(s) did not finish in {timeout}s.")


app = FastAPI(title="Stingray", lifespan=lifespan)
app.add_middleware(OriginGuardMiddleware)


# -- Articles --


@app.get("/api/articles")
def get_articles(
  request: Request,
  feed_id: int | None = Query(None),
  unread: bool = Query(False),
  since_days: int | None = Query(None, ge=1, le=3650),
  # Only with `unread`: also keep rows read within this many hours.
  read_within_hours: int | None = Query(None, ge=1, le=168),
  limit: int = Query(10000, ge=1, le=10000),
) -> list[ArticleRow]:
  config: AppConfig = request.app.state.config  # type: ignore[attr-defined]
  return repo.list_articles(
    feed_id=feed_id,
    unread=unread,
    since_days=since_days,
    read_within_hours=read_within_hours,
    limit=limit,
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
  folder_id: int | None = Query(None),
  older_than_hours: int | None = Query(None, ge=1),
) -> dict[str, int]:
  count = repo.mark_all_read(feed_id, older_than_hours, folder_id)
  return {"marked": count}


@app.post("/api/articles/unread-all")
def mark_all_articles_unread(
  feed_id: int | None = Query(None),
  folder_id: int | None = Query(None),
) -> dict[str, int]:
  count = repo.mark_all_unread(feed_id, folder_id)
  return {"unmarked": count}


# -- Folders --


# Trimmed before validation, so a blank or whitespace-only name is a 422.
FolderName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class FolderCreate(BaseModel):
  name: FolderName


class FolderRename(BaseModel):
  name: FolderName


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
  return "<html" in prefix


async def _probe_feed(url: str, *, native_lang: str, fetch_settings: FetchSettings) -> ProbeResult:
  """Fetch a feed URL and extract its title, translate flag, and site URL.

  If the URL is not a valid RSS/Atom feed, detect it as a web page.
  """
  try:
    if url.startswith("file://"):
      # Debug-only: local file is always treated as RSS/Atom, skip HTML detection.
      body = await asyncio.to_thread(read_file_url, url)
    else:
      async with SafeClient(fetch_settings, timeout=15) as client:
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


async def _fetch_single_feed(feed: FeedRow, config: AppConfig) -> None:
  """Fetch articles from a single feed and persist to DB.

  Fetches and classifies the result the same way as the scheduled path, but
  records it with update_feed_fetch_status so the adaptive schedule is left
  alone. Unlike the scheduled path, no age cutoff is applied.
  """
  start = time.perf_counter()
  try:
    clean_url_fn = clean_url if config.url_cleanup.enabled else None
    feed_cfg = feed.to_feed_cfg()
    feed_cfg["max_items"] = config.max_items_per_feed
    log.step(f"Fetching feed: {feed.name} ({feed.url})")
    fetch_settings = FetchSettings.from_config(config)
    async with SafeClient(fetch_settings, timeout=30) as client:
      articles, _was_cached, kind, source_tag = await fetch_one_feed(
        client,
        feed_cfg,
        clean_url_fn=clean_url_fn,
      )
    new_count = 0
    if articles:
      new_count = await asyncio.to_thread(repo.upsert_articles, articles)
      log.info(f"  Saved: {new_count} new / {len(articles)} total (existing skipped).")
    # Unlike fetcher._backfill_site_urls, web feeds are probed too: once their
    # page is fetched, _probe_feed fills site_url with the page URL in one go.
    if kind != "web-norules" and not feed.site_url and feed.url:
      probe = await _probe_feed(
        feed.url,
        native_lang=config.native_lang,
        fetch_settings=fetch_settings,
      )
      if probe.site_url:
        await asyncio.to_thread(repo.update_feed_site_url, feed.id, probe.site_url)
        log.info(f"  Updated site_url: {probe.site_url}")
    outcome, error = classify_outcome(source_tag, kind, None, None, new_count)
    health: FeedHealth = "degraded" if outcome == "degraded" else "ok"
    await asyncio.to_thread(repo.update_feed_fetch_status, feed.id, health=health, error=error)
    log.success(f"  Done: {feed.name}")
  except Exception as e:
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    log.error(f"  Failed [{feed.name}] after {elapsed_ms}ms: {type(e).__name__}: {e}")
    await asyncio.to_thread(repo.update_feed_fetch_status, feed.id, health="failing", error=str(e))


@app.post("/api/feeds", status_code=201)
async def create_feed(body: FeedCreate, request: Request) -> FeedRow:
  config: AppConfig = request.app.state.config  # type: ignore[attr-defined]
  log.step(f"Probing feed: {body.url}")
  probe = await _probe_feed(
    body.url,
    native_lang=config.native_lang,
    fetch_settings=FetchSettings.from_config(config),
  )
  if not body.name.strip():
    body.name = probe.title or body.url
  if not body.translate:
    body.translate = probe.translate
  # Foreign feeds detected by the probe also get summarize enabled so the body
  # is rendered in the native language right after adding — translate alone only
  # translates titles for long articles. Native feeds keep summarize opt-in.
  # The probe only claims "foreign" on a positive detection, so a feed whose
  # language cannot be determined lands here with both flags left untouched.
  if probe.translate:
    body.summarize = True

  extraction_rules = None
  if probe.is_web_page:
    # If the page advertises feeds via <link rel="alternate">, hand them back
    # to the client instead of silently creating a web-scrape placeholder —
    # the user almost certainly wants to subscribe to the real feed.
    if probe.feed_candidates:
      log.info(
        f"  HTML page with {len(probe.feed_candidates)} feed candidate(s). Returning for user selection."
      )
      raise HTTPException(
        422,
        detail={
          "message": "This URL is an HTML page. Pick a feed below.",
          "candidates": probe.feed_candidates,
          "page_title": probe.title,
        },
      )
    extraction_rules = "{}"
    log.info(
      "  Web page detected with no feed candidates. Extraction rules can be set in feed settings."
    )
  else:
    if not probe.title and not probe.site_url:
      raise HTTPException(
        422, "Could not parse this URL. Check the URL and server logs for details."
      )
    log.info(f"  RSS feed: {probe.title or '(no title)'}")

  feed = FeedRow(**body.model_dump(), site_url=probe.site_url, extraction_rules=extraction_rules)
  try:
    created = await asyncio.to_thread(repo.add_feed, feed)
  except repo.DuplicateFeedError as exc:
    raise HTTPException(409, str(exc)) from exc
  log.info(f"  Feed created: id={created.id}, name={created.name}")
  # Web-scrape feeds are created with empty rules ("{}"); fetching now would
  # only be skipped and marked degraded. Defer the first fetch until rules are
  # inferred and saved. RSS/Atom feeds fetch immediately.
  if extraction_rules is None:
    _spawn_background(_fetch_single_feed(created, config))
  return created


async def _delete_all_data_locked() -> None:
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
      except asyncio.CancelledError:
        pass
      except Exception as e:
        log.warn(f"Cancelled refresh raised while clearing data: {type(e).__name__}: {e}")
    _refresh_task = None
    # Deleting inside the lock: to_thread keeps the event loop free, so a
    # concurrent POST /api/refresh could otherwise start repopulating the DB
    # while the delete is still running. Both refresh entry points take this
    # same lock. A refresh cancelled just above can still land a write from a
    # to_thread call already in flight (cancelling the awaiter does not stop
    # the thread) — this narrows that window rather than closing it.
    await asyncio.to_thread(repo.delete_all_data)
    purged = await asyncio.to_thread(cache.purge_feed_cache)
  if purged:
    log.step(f"Purged {purged} cached feed bodies.")


@app.delete("/api/feeds", status_code=204)
async def delete_all_data() -> None:
  # Shielded, and the lock lives inside: cancelling the request must not
  # release _refresh_lock while a to_thread delete is still running in a
  # worker, or a refresh could start repopulating the DB mid-delete. Shielding
  # the awaits alone would not help — the `async with` would still unwind.
  await asyncio.shield(_delete_all_data_locked())


@app.post("/api/feeds/{feed_id}/fetch", status_code=202)
async def fetch_feed(feed_id: int, request: Request) -> dict[str, str]:
  feed = await asyncio.to_thread(repo.get_feed_by_id, feed_id)
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
  the response body matches the post-update state.

  Stays sync because the `def` handlers below call it from FastAPI's own
  threadpool. Async handlers must wrap it in asyncio.to_thread — it hits the DB.
  """
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
  feed = await asyncio.to_thread(_get_feed_or_404, feed_id)
  if feed.extraction_rules is None:
    raise HTTPException(400, "Not a web page feed")
  try:
    rules_raw: object = await request.json()
  except (ValueError, RecursionError) as e:
    # ValueError covers bad syntax, undecodable bytes and over-long integers;
    # deep nesting overflows json.loads.
    raise HTTPException(422, "Request body must be valid JSON") from e
  try:
    rules = validate_extraction_rules(rules_raw)
  except ValueError as e:
    raise HTTPException(422, str(e)) from e
  await asyncio.to_thread(repo.update_feed_extraction_rules, feed_id, json.dumps(rules))
  return await asyncio.to_thread(_get_feed_or_404, feed_id)


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
  feed = await asyncio.to_thread(_get_feed_or_404, feed_id)
  if feed.extraction_rules is None:
    raise HTTPException(400, "Not a web page feed")
  if not feed.url:
    raise HTTPException(400, "Feed has no URL")
  config: AppConfig = request.app.state.config  # type: ignore[attr-defined]
  if not config.ollama.enabled:
    raise HTTPException(503, "LLM is disabled (ollama.enabled=false)")
  ollama_url = os.environ.get("OLLAMA_BASE_URL") or config.ollama.base_url
  # to_thread: _probe_llm blocks for as long as Ollama takes to answer, and this
  # handler is async. get_status() below calls it directly because FastAPI runs
  # sync handlers in its own threadpool already.
  llm_ok, llm_err = await asyncio.to_thread(_probe_llm, ollama_url)
  if not llm_ok:
    raise HTTPException(503, f"LLM is unavailable: {llm_err}")

  # Fetch the page, truncating the download and using the final (post-redirect)
  # URL as the base for resolving relative article links.
  try:
    async with SafeClient(FetchSettings.from_config(config), timeout=30) as client:
      resp = await client.get(
        feed.url,
        max_bytes=_INFER_MAX_HTML_FETCH_BYTES,
        truncate=True,
      )
      resp.raise_for_status()
      html = resp.content.decode(resp.encoding or "utf-8", errors="ignore")
      page_url = str(resp.url)
  except httpx.HTTPError as e:
    raise HTTPException(502, f"Failed to fetch page: {e}") from e

  clean_url_fn = clean_url if config.url_cleanup.enabled else None
  async with (
    _infer_semaphore,
    httpx.AsyncClient(
      base_url=ollama_url,
      timeout=config.ollama.timeout,
    ) as ollama_client,
  ):
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
      SampleArticle(title=a.title, url=a.url, published=a.published) for a in result.sample_articles
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


FilterTarget = Literal["title", "both", "url"]


class FilterCreate(BaseModel):
  pattern: str = Field(min_length=1)
  target: FilterTarget = "title"


@app.get("/api/filters")
def get_filters() -> list[FilterRow]:
  return repo.list_filters()


@app.post("/api/filters", status_code=201)
def create_filter(body: FilterCreate) -> FilterRow:
  pattern = body.pattern.strip()
  if not pattern:
    raise HTTPException(400, "Pattern must not be empty")
  try:
    return repo.add_filter(pattern, body.target)
  except repo.DuplicateFilterError as exc:
    raise HTTPException(409, str(exc)) from exc


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


def _persist_filters(items: list[tuple[str, str]]) -> tuple[int, int]:
  """Insert filter rules, skipping ones equivalent to a rule already stored.

  Returns (created, skipped). Mirrors _persist_opml_feeds: a plain loop of
  individual INSERTs pulled out so the caller can hand it to a worker thread.
  Needs no lock of its own — repo.add_filter rejects duplicates with an
  advisory lock and a UNIQUE index, and DuplicateFilterError is counted as a
  skip. Not one transaction either: each add_filter commits on its own.
  """
  existing = {repo.filter_key(f.pattern, f.target) for f in repo.list_filters()}
  created = 0
  skipped = 0
  for pattern, target in items:
    key = repo.filter_key(pattern, target)
    if key in existing:
      skipped += 1
      continue
    try:
      repo.add_filter(pattern, target)
    except repo.DuplicateFilterError:
      skipped += 1
      continue
    existing.add(key)
    created += 1
  return created, skipped


@app.post("/api/filters/import")
async def import_filters(file: UploadFile) -> dict[str, int]:
  try:
    content = (await file.read()).decode("utf-8")
    data: object = json.loads(content)
  except (UnicodeDecodeError, json.JSONDecodeError) as exc:
    raise HTTPException(400, f"Invalid JSON: {exc}") from exc
  if not isinstance(data, list):
    raise HTTPException(400, "Expected a JSON array")
  # Malformed entries are dropped here rather than counted: they were never
  # rules, so they are neither created nor skipped.
  items: list[tuple[str, str]] = []
  for raw_item in cast(list[object], data):
    if not isinstance(raw_item, dict) or "pattern" not in raw_item:
      continue
    item = cast(dict[str, object], raw_item)
    pattern = str(item["pattern"]).strip()
    target = str(item.get("target", "title")).strip()
    if not pattern or target not in get_args(FilterTarget):
      continue
    items.append((pattern, target))
  created, skipped = await asyncio.to_thread(_persist_filters, items)
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
  """Create any missing folders/feeds from parsed OPML in one transaction.

  Returns (folders_created, feeds_created, feeds_skipped). Feeds whose URL
  already exists in the DB are skipped rather than re-added.
  """

  def _to_row(feed_data: ImportFeed) -> FeedRow:
    return FeedRow(
      name=feed_data.name,
      url=feed_data.url,
      site_url=feed_data.site_url,
      translate=feed_data.translate,
      summarize=feed_data.summarize,
      extraction_rules=feed_data.extraction_rules,
    )

  return repo.import_opml(
    [(f.name, [_to_row(feed) for feed in f.feeds]) for f in imported_folders],
    [_to_row(feed) for feed in uncategorized],
  )


async def _persist_opml_import(
  imported_folders: list[ImportFolder],
  uncategorized: list[ImportFeed],
) -> tuple[int, int, int]:
  """Persist a parsed OPML import, serialized against other imports.

  Duplicate feed URLs are caught by the unique index, but folders have no
  such constraint: two concurrent imports would each see a folder missing and
  create it twice. Guards a single process only — a multi-worker deployment
  needs the DB to enforce it.

  Callers must shield this: the lock has to outlive the request, or cancelling
  it would free the lock while the worker is still inserting. Shielding the
  to_thread call alone would not do — the `async with` would still unwind.

  """
  async with _opml_import_lock:
    return await asyncio.to_thread(
      _persist_opml_feeds,
      imported_folders,
      uncategorized,
    )


async def _start_refresh(config: AppConfig, *, trigger: str, force: bool = False) -> None:
  """Create the refresh_jobs row and spawn the refresh task as one unit.

  Callers must shield this. The row is inserted from a worker thread, so a
  cancellation between the INSERT and create_task() would leave the job stuck
  at 'running' with nothing to finish it — /api/status would then report a
  refresh that is not happening, with no way back short of a restart.
  """
  global _refresh_task
  job_id = await asyncio.to_thread(repo.create_refresh_job, trigger)
  _refresh_task = asyncio.create_task(
    _run_refresh(config, trigger=trigger, job_id=job_id, force=force)
  )
  _background_tasks.add(_refresh_task)
  _refresh_task.add_done_callback(_log_task_exception)


async def _maybe_trigger_opml_refresh(request: Request, feeds_created: int) -> None:
  """Kick off a refresh if OPML import added new feeds, without blocking the
  response. The refresh_jobs row is awaited before returning so /api/status
  reports running=true as soon as this response reaches the client.
  """
  if feeds_created <= 0:
    return
  async with _refresh_lock:
    if _refresh_task is not None and not _refresh_task.done():
      return
    config: AppConfig = request.app.state.config  # type: ignore[attr-defined]
    log.info(f"OPML import added {feeds_created} feeds, auto-triggering refresh.")
    await asyncio.shield(_start_refresh(config, trigger="opml"))


@app.post("/api/opml/import")
async def opml_import(file: UploadFile, request: Request) -> dict[str, int]:
  config: AppConfig = request.app.state.config  # type: ignore[attr-defined]
  try:
    content = (await file.read()).decode("utf-8")
    imported_folders, uncategorized = parse_opml(content, native_lang=config.native_lang)
  except UnicodeDecodeError as e:
    raise HTTPException(400, f"Invalid file encoding: {e}") from e
  except _ET.ParseError as e:
    raise HTTPException(400, f"Invalid OPML XML: {e}") from e

  folders_created, feeds_created, feeds_skipped = await asyncio.shield(
    _persist_opml_import(imported_folders, uncategorized)
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
  job_id: int,
  force: bool = False,
) -> None:
  """Run a web-triggered refresh whose refresh_jobs row is already created.

  refresh_all closes the row on success and on errors, but cancellation
  (shutdown, or the delete-all endpoint) is not an Exception and may land
  before refresh_all is even reached, while waiting for the summarizer lock.
  Closing the row here covers both, so /api/status does not report a refresh
  that is no longer running.
  """
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
  except asyncio.CancelledError:
    log.warn(f"Refresh cancelled (trigger={trigger}).")
    await asyncio.to_thread(repo.finish_refresh_job, job_id, "failed", 0, "cancelled")
    raise
  finally:
    _refresh_task = None


@app.post("/api/refresh")
async def trigger_refresh(request: Request) -> JSONResponse:
  async with _refresh_lock:
    if _refresh_task is not None and not _refresh_task.done():
      log.info("Refresh requested but one is already in progress.")
      return JSONResponse({"message": "Refresh already in progress"}, status_code=409)
    config: AppConfig = request.app.state.config  # type: ignore[attr-defined]
    # Create the refresh_jobs row before returning so /api/status reports
    # running=true by the time this response reaches the client.
    await asyncio.shield(_start_refresh(config, trigger="web", force=True))
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
    return StatusResponse(
      running=False, llm_enabled=llm_enabled, llm_available=llm_ok, llm_error=llm_err
    )
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
    raise HTTPException(status_code=503, detail=f"db: {type(e).__name__}: {e}") from e
  return {"status": "ok"}


# -- SPA fallback --
# Mount built frontend if it exists, otherwise skip.

_frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
_frontend_root = _frontend_dist.resolve()


def _resolve_spa_file(path: str) -> Path | None:
  """Resolve a request path to a file inside the dist dir, or None.

  is_relative_to() rather than a string prefix check: "dist-old" starts with
  "dist" as text but is a different directory.
  """
  file = (_frontend_dist / path).resolve()
  if not file.is_relative_to(_frontend_root):
    return None
  return file if file.is_file() else None


if _frontend_dist.is_dir():
  app.mount("/assets", StaticFiles(directory=str(_frontend_dist / "assets")), name="static")

  @app.get("/{path:path}")
  async def spa_fallback(path: str) -> FileResponse:
    # Every page load hits this, so keep the stat() calls off the event loop.
    file = await asyncio.to_thread(_resolve_spa_file, path) if path else None
    if file is not None:
      return FileResponse(str(file))
    return FileResponse(str(_frontend_dist / "index.html"))
