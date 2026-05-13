import asyncio
import hashlib
import html
import json
import random
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable
from urllib.parse import urljoin

import feedparser
import httpx
from bs4 import BeautifulSoup
from pathlib import Path

import lang
import log
from cache import load_feed_cache, save_feed_cache
from models import Article
from scraper import fetch_web_page


# Link types on <link rel="alternate"> that identify a feed. Order matters:
# preferred types come first so a page advertising both RSS and Atom surfaces
# its RSS link at the top of the list.
_FEED_LINK_TYPES = (
  "application/rss+xml",
  "application/atom+xml",
  "application/rdf+xml",
  "application/feed+json",
  "application/json",
)


# Base directory for `file://` feed URLs. Relative paths in a `file://` URL
# are resolved against this directory. Debug-only; mounted read-only via
# compose.yml.
DEBUG_FEEDS_DIR = Path("/app/debug-feeds")


def read_file_url(url: str) -> str:
  """Read body of a `file://` feed URL (debug-only).

  Absolute paths (`file:///abs/path`) are used as-is. Relative paths
  (`file://name.xml`, `file://./name.xml`) resolve against DEBUG_FEEDS_DIR.
  """
  raw = url.removeprefix("file://")
  path = Path(raw) if raw.startswith("/") else DEBUG_FEEDS_DIR / raw
  return path.read_text(encoding="utf-8")


def extract_feed_candidates(body: str, base_url: str) -> list[dict[str, str]]:
  """Extract feed candidates from an HTML page via <link rel="alternate">.

  Returns a list of {href, title, type} dicts with duplicates removed.
  The href is resolved against base_url so callers receive absolute URLs.
  """
  soup = BeautifulSoup(body, "html.parser")
  seen: set[str] = set()
  candidates: list[dict[str, str]] = []
  for type_hint in _FEED_LINK_TYPES:
    for link in soup.find_all("link", rel=lambda v: v and "alternate" in v):
      link_type = (link.get("type") or "").strip().lower()
      if link_type != type_hint:
        continue
      href = (link.get("href") or "").strip()
      if not href:
        continue
      absolute = urljoin(base_url, href)
      if absolute in seen:
        continue
      seen.add(absolute)
      candidates.append({
        "href": absolute,
        "title": (link.get("title") or "").strip(),
        "type": link_type,
      })
  return candidates


# ---------------------------------------------------------------------------
# Typed wrappers around feedparser for callers that are pyright-strict.
# ---------------------------------------------------------------------------


def probe_feed_body(body: str) -> tuple[str | None, str | None, str | None, bool]:
  """Parse a feed body and return (title, site_url, detected_lang, has_entries).

  Attempts feed-level language detection using:
    - Feed `language` attribute
    - Japanese kana in the first few entry titles
  Returns detected_lang=None if none of the heuristics succeed.
  """
  parsed = feedparser.parse(body)
  if not parsed.entries:
    return None, None, None, False

  feed_meta = parsed.feed or {}
  raw_title = feed_meta.get("title", "") or ""
  raw_link = feed_meta.get("link", "") or ""
  title = raw_title.strip() or None
  site_url = raw_link.strip() or None

  feed_lang = feed_meta.get("language", "") or ""
  detected_lang = lang.normalize_lang_code(feed_lang)

  if detected_lang is None:
    for entry in parsed.entries[:5]:
      entry_title = entry.get("title", "") or ""
      detected_lang = lang.detect_lang_by_script(entry_title)
      if detected_lang is not None:
        break

  return title, site_url, detected_lang, True


def extract_site_url(body: str) -> str | None:
  """Return the <link> of a feed body, or None if parsing fails."""
  parsed = feedparser.parse(body)
  site_url = (parsed.feed or {}).get("link", "") or ""
  stripped = site_url.strip()
  return stripped or None


def _strip_html(text: str) -> str:
  text = html.unescape(text)
  text = re.sub(r"<[^>]+>", "", text)
  text = re.sub(r"(Article|Comments) URL: https?://\S+\n?", "", text)
  text = re.sub(r"Points: \d+\n?", "", text)
  text = re.sub(r"# Comments: \d+\n?", "", text)
  return text.strip()


def _truncate(text: str, max_len: int = 500) -> str:
  if len(text) <= max_len:
    return text
  return text[:max_len] + "..."


async def _fetch_with_cache(
  client: httpx.AsyncClient,
  url: str,
  headers: dict | None = None,
) -> tuple[str, bool, str]:
  """Fetch URL with L1 cache (ETag / content hash).

  Returns (body, was_cached, source_tag) where source_tag is a short
  human-readable label of where the body came from:
    - "fresh"     : 200 OK, body fetched and saved
    - "unchanged" : body unchanged by content hash (served from cache)
    - "304"       : server returned 304 Not Modified
    - "304-empty" : 304 but no cached copy available → body is empty
    - "net-cache" : network error, cached copy served
    - "5xx-cache" : HTTP 4xx/5xx, cached copy served
    - "file"      : body read from a local file (debug-only file:// URL)
  """
  if url.startswith("file://"):
    body = read_file_url(url)
    log.dim(f"    FILE {url} → {len(body.encode('utf-8')) / 1024:.1f}KB")
    return body, False, "file"

  cached = load_feed_cache(url)
  if cached and "body" not in cached:
    cached = None
  req_headers = dict(headers or {})
  conditional = False
  if cached:
    if cached.get("etag"):
      req_headers["If-None-Match"] = cached["etag"]
      conditional = True
    if cached.get("last_modified"):
      req_headers["If-Modified-Since"] = cached["last_modified"]
      conditional = True

  start = time.perf_counter()
  try:
    resp = await client.get(url, headers=req_headers)
  except (httpx.TransportError, OSError) as e:
    if cached:
      log.warn(f"    Network error, using cached feed: {url} ({type(e).__name__}: {e or 'no details'})")
      return cached["body"], True, "net-cache"
    raise
  elapsed_ms = int((time.perf_counter() - start) * 1000)

  if resp.status_code == 304:
    if cached:
      log.dim(f"    GET {url} → 304 Not Modified ({elapsed_ms}ms, cache hit)")
      return cached["body"], True, "304"
    log.warn(f"    304 without cached data, skipping: {url}")
    return "", True, "304-empty"

  try:
    resp.raise_for_status()
  except httpx.HTTPStatusError as e:
    if e.response.status_code in {403, 429, 500, 502, 503, 504} and cached:
      log.warn(f"    HTTP {e.response.status_code}, using cached feed: {url}")
      return cached["body"], True, "5xx-cache"
    raise
  body = resp.text
  size_kb = len(body.encode("utf-8")) / 1024

  if cached and hashlib.sha256(body.encode()).hexdigest() == cached.get("content_hash"):
    log.dim(f"    GET {url} → {resp.status_code} {size_kb:.1f}KB ({elapsed_ms}ms, unchanged)")
    return body, True, "unchanged"

  etag = resp.headers.get("etag")
  last_modified = resp.headers.get("last-modified")
  save_feed_cache(url, etag, last_modified, body)
  log.dim(
    f"    GET {url} → {resp.status_code} {size_kb:.1f}KB ({elapsed_ms}ms"
    f"{', conditional' if conditional else ''})"
  )
  return body, False, "fresh"


def _extract_item_images(body: str) -> dict[str, str]:
  """Extract thumbnail images from RSS/Atom XML that feedparser ignores.

  Checks: <image>, <media:content>, <media:thumbnail>, <enclosure type="image/*">.
  Returns a dict mapping item link URL to image URL.
  """
  images: dict[str, str] = {}
  try:
    root = ET.fromstring(body)
  except ET.ParseError:
    return images

  ns = {
    "media": "http://search.yahoo.com/mrss/",
    "atom": "http://www.w3.org/2005/Atom",
  }

  # RSS items
  for item in root.iter("item"):
    link_el = item.find("link")
    link = link_el.text.strip() if link_el is not None and link_el.text else None
    if not link:
      continue
    # <image>url</image> (non-standard, e.g. wired.jp)
    img_el = item.find("image")
    if img_el is not None and img_el.text and img_el.text.strip():
      images[link] = img_el.text.strip()
      continue
    # <media:content url="..."/>
    media = item.find("media:content", ns)
    if media is not None and media.get("url"):
      images[link] = media.get("url", "")
      continue
    # <media:thumbnail url="..."/>
    thumb = item.find("media:thumbnail", ns)
    if thumb is not None and thumb.get("url"):
      images[link] = thumb.get("url", "")
      continue
    # <enclosure type="image/..." url="..."/>
    enc = item.find("enclosure")
    if enc is not None and (enc.get("type", "").startswith("image/")) and enc.get("url"):
      images[link] = enc.get("url", "")

  # Atom entries
  for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
    link_el = entry.find("atom:link[@rel='alternate']", ns)
    if link_el is None:
      link_el = entry.find("atom:link", ns)
    link = link_el.get("href", "").strip() if link_el is not None else ""
    if not link:
      continue
    media = entry.find("media:content", ns)
    if media is not None and media.get("url"):
      images[link] = media.get("url", "")
      continue
    thumb = entry.find("media:thumbnail", ns)
    if thumb is not None and thumb.get("url"):
      images[link] = thumb.get("url", "")

  return images


def _parse_rss(
  body: str,
  feed_cfg: dict,
  clean_url_fn: Callable[[str], str] | None = None,
) -> list[Article]:
  max_items = feed_cfg.get("max_items", 200)
  source = feed_cfg["name"]
  parsed = feedparser.parse(body)
  item_images = _extract_item_images(body)

  articles = []
  for entry in parsed.entries[:max_items]:
    published = None
    for attr in ("published_parsed", "updated_parsed"):
      if hasattr(entry, attr) and getattr(entry, attr):
        try:
          published = datetime(*getattr(entry, attr)[:6], tzinfo=timezone.utc)
          break
        except (ValueError, TypeError):
          pass

    raw_html = ""
    snippet = ""
    # Prefer content:encoded (full article) over description (often truncated)
    if hasattr(entry, "content") and entry.content:
      raw_html = entry.content[0].get("value", "")
    if not raw_html and hasattr(entry, "summary"):
      raw_html = entry.summary
    if not raw_html and hasattr(entry, "description"):
      raw_html = entry.description
    if raw_html:
      snippet = _strip_html(raw_html)

    raw_link = entry.get("link", "")
    if not raw_link or not raw_link.startswith(("http://", "https://")):
      continue
    cleaned_link = clean_url_fn(raw_link) if clean_url_fn else raw_link

    # Inject thumbnail image into content_html if not already present.
    # `item_images` was populated from the same raw <link>, so lookup
    # uses the raw value — never the cleaned one.
    thumb_url = item_images.get(raw_link)
    if thumb_url and thumb_url not in raw_html:
      raw_html = f'<img src="{html.escape(thumb_url)}" alt="" />\n{raw_html}'

    title = html.unescape(entry.get("title", "(no title)"))
    articles.append(Article(
      title=title,
      url=cleaned_link,
      source=source,
      published=published,
      content_snippet=_truncate(snippet),
      content_html=raw_html,
    ))

  return articles


async def _fetch_rss(
  client: httpx.AsyncClient,
  feed_cfg: dict,
  clean_url_fn: Callable[[str], str] | None = None,
) -> tuple[list[Article], bool, str]:
  """Fetch an RSS feed body with caching and parse it.

  Returns (articles, was_cached, source_tag). The source_tag is forwarded
  from _fetch_with_cache so schedulers downstream can distinguish a normal
  fetch from a degraded cache fallback.
  """
  url = feed_cfg["url"]
  body, was_cached, tag = await _fetch_with_cache(client, url)
  # Empty body happens on 304-without-cache (already logged in _fetch_with_cache).
  # Returning [] here lets callers treat it the same as "no new articles".
  if not body:
    return [], was_cached, tag
  return _parse_rss(body, feed_cfg, clean_url_fn=clean_url_fn), was_cached, tag


_CONCURRENCY = 5

async def _delayed_fetch(
  client,
  feed,
  delay: float,
  sem: asyncio.Semaphore,
  clean_url_fn: Callable[[str], str] | None = None,
):
  """Fetch a single feed with concurrency limit and random delay.

  Returns (articles, was_cached, kind, source_tag) where
    - `kind` is a short label indicating how the feed was fetched
      ("rss" / "web" / "web-norules") — used for per-feed summary logging.
    - `source_tag` is the low-level fetch tag from _fetch_with_cache for RSS
      feeds (e.g. "fresh", "unchanged", "304", "304-empty", "net-cache",
      "5xx-cache"); None for web feeds (they don't go through the cache layer).
  """
  name = feed.get("name") or feed.get("url") or "?"
  async with sem:
    if delay > 0:
      await asyncio.sleep(delay)
    rules_json = feed.get("extraction_rules")
    is_web = rules_json is not None
    if is_web and rules_json != "{}":
      rules = json.loads(rules_json) if isinstance(rules_json, str) else rules_json
      if rules.get("item"):
        start = time.perf_counter()
        articles, was_cached = await fetch_web_page(
          client, feed, clean_url_fn=clean_url_fn,
        )
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        source = "cache" if was_cached else "fresh"
        log.info(f"  [web/{source}] {name}: {len(articles)} items ({elapsed_ms}ms)")
        return articles, was_cached, "web", None
      log.warn(f"  [web/skip] {name}: extraction rules incomplete")
      return [], False, "web-norules", None
    if is_web:
      log.warn(f"  [web/skip] {name}: no extraction rules configured")
      return [], False, "web-norules", None
    start = time.perf_counter()
    articles, was_cached, source_tag = await _fetch_rss(
      client, feed, clean_url_fn=clean_url_fn,
    )
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    source = "cache" if was_cached else "fresh"
    log.info(f"  [rss/{source}] {name}: {len(articles)} items ({elapsed_ms}ms)")
    return articles, was_cached, "rss", source_tag


def _filter_by_cutoff(feed_articles: list[Article], cutoff: datetime) -> list[Article]:
  """Normalize published tz and drop items older than cutoff.

  Mutates `a.published` in place to ensure tz-awareness so downstream code
  (DB layer, post-gather aggregation) sees consistent values.
  """
  fresh: list[Article] = []
  for a in feed_articles:
    pub = a.published
    if pub is not None and pub.tzinfo is None:
      pub = pub.replace(tzinfo=timezone.utc)
      a.published = pub
    if pub is None or pub >= cutoff:
      fresh.append(a)
  return fresh


OnFeedDone = Callable[
  [dict, list[Article], str | None, str, Exception | None],
  Awaitable[None],
]


async def fetch_all(
  feeds_cfg: list[dict],
  max_age_hours: float = 48,
  max_items: int = 200,
  on_feed_done: OnFeedDone | None = None,
  clean_url_fn: Callable[[str], str] | None = None,
) -> list[Article]:
  """Fetch every feed in parallel.

  If `on_feed_done` is given, it is invoked exactly once per feed — on
  success, on failure, and on empty results alike — with:
    (feed_cfg, articles, source_tag, feed_kind, fetch_exc)

  `articles` is the age-cutoff-filtered list; empty on failure or when the
  feed had nothing in the window. `source_tag` is non-None only for RSS
  feeds. `fetch_exc` is non-None only when the fetch itself raised.

  Callers use the callback to persist incrementally (so the UI sees rows
  appear feed-by-feed) and to record schedule outcomes for adaptive fetch
  intervals.
  """
  sem = asyncio.Semaphore(_CONCURRENCY)
  for feed in feeds_cfg:
    feed.setdefault("max_items", max_items)
  cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=max_age_hours)

  async def _run_one(client: httpx.AsyncClient, feed: dict):
    try:
      articles, was_cached, kind, source_tag = await _delayed_fetch(
        client, feed, random.uniform(0, 2), sem,
        clean_url_fn=clean_url_fn,
      )
      fetch_exc: Exception | None = None
    except Exception as e:
      log.error(f"  [error] {feed.get('name')}: {type(e).__name__}: {e}")
      articles, was_cached, kind, source_tag = [], False, "rss", None
      fetch_exc = e

    fresh = _filter_by_cutoff(articles, cutoff) if articles else []

    if on_feed_done is not None:
      try:
        await on_feed_done(feed, fresh, source_tag, kind, fetch_exc)
      except Exception as e:
        log.error(f"  [persist-error] {feed.get('name')}: {type(e).__name__}: {e}")

    return fresh, was_cached, kind, fetch_exc

  async with httpx.AsyncClient(
    timeout=httpx.Timeout(60, connect=30),
    follow_redirects=True,
    limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
  ) as client:
    tasks = [_run_one(client, feed) for feed in feeds_cfg]
    results = await asyncio.gather(*tasks)

  return _aggregate_feed_results(feeds_cfg, results)


def _aggregate_feed_results(
  feeds_cfg: list[dict],
  results: list[tuple[list[Article], bool, str, Exception | None]],
) -> list[Article]:
  """Merge per-feed fetch outcomes into a deduped article list and log a
  one-line summary. Articles are already cutoff-filtered by _run_one.
  """
  articles: list[Article] = []
  seen_urls: set[str] = set()
  cached_feeds = 0
  fetched_feeds = 0
  error_feeds = 0
  skipped_web_feeds = 0
  total_items = 0
  for _feed, (feed_articles, was_cached, kind, fetch_exc) in zip(feeds_cfg, results):
    if fetch_exc is not None:
      error_feeds += 1
    elif kind == "web-norules":
      skipped_web_feeds += 1
    elif was_cached:
      cached_feeds += 1
    else:
      fetched_feeds += 1
    total_items += len(feed_articles)
    for a in feed_articles:
      if a.url in seen_urls:
        continue
      seen_urls.add(a.url)
      articles.append(a)

  log.info(
    f"  Feeds: {fetched_feeds} fresh, {cached_feeds} cached"
    f"{f', {error_feeds} errors' if error_feeds else ''}"
    f"{f', {skipped_web_feeds} skipped (no rules)' if skipped_web_feeds else ''}"
    f" | items: {len(articles)} within window, {total_items} total."
  )
  return articles
