import asyncio
import hashlib
import html
import json
import random
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import feedparser
import httpx

import log
from cache import load_feed_cache, save_feed_cache
from models import JA_KANA, Article
from scraper import fetch_web_page


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

  detected_lang: str | None = None
  feed_lang = feed_meta.get("language", "") or ""
  if feed_lang:
    code = feed_lang.split("-")[0].lower()
    if len(code) == 2:
      detected_lang = code

  if detected_lang is None:
    for entry in parsed.entries[:5]:
      entry_title = entry.get("title", "") or ""
      if entry_title and JA_KANA.search(entry_title):
        detected_lang = "ja"
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


def _detect_lang(title: str, default: str) -> str:
  if title and JA_KANA.search(title):
    return "ja"
  return default


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
  """
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


def _parse_rss(body: str, feed_cfg: dict) -> list[Article]:
  max_items = feed_cfg.get("max_items", 20)
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

    link = entry.get("link", "")
    if not link or not link.startswith(("http://", "https://")):
      continue

    # Inject thumbnail image into content_html if not already present
    thumb_url = item_images.get(link)
    if thumb_url and thumb_url not in raw_html:
      raw_html = f'<img src="{html.escape(thumb_url)}" alt="" />\n{raw_html}'

    title = entry.get("title", "(no title)")
    articles.append(Article(
      title=title,
      url=link,
      source=source,
      published=published,
      content_snippet=_truncate(snippet),
      content_html=raw_html,
    ))

  return articles


async def _fetch_rss(client: httpx.AsyncClient, feed_cfg: dict) -> tuple[list[Article], bool]:
  url = feed_cfg["url"]
  body, was_cached, _tag = await _fetch_with_cache(client, url)
  # Empty body happens on 304-without-cache (already logged in _fetch_with_cache).
  # Returning [] here lets callers treat it the same as "no new articles".
  if not body:
    return [], was_cached
  return _parse_rss(body, feed_cfg), was_cached


_CONCURRENCY = 5

async def _delayed_fetch(client, feed, delay: float, sem: asyncio.Semaphore):
  """Fetch a single feed with concurrency limit and random delay.

  Returns (articles, was_cached, kind) where `kind` is a short label
  indicating how the feed was fetched — used for per-feed summary logging.
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
        articles, was_cached = await fetch_web_page(client, feed)
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        source = "cache" if was_cached else "fresh"
        log.info(f"  [web/{source}] {name}: {len(articles)} items ({elapsed_ms}ms)")
        return articles, was_cached, "web"
      log.warn(f"  [web/skip] {name}: extraction rules incomplete")
      return [], False, "web-norules"
    if is_web:
      log.warn(f"  [web/skip] {name}: no extraction rules configured")
      return [], False, "web-norules"
    start = time.perf_counter()
    articles, was_cached = await _fetch_rss(client, feed)
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    source = "cache" if was_cached else "fresh"
    log.info(f"  [rss/{source}] {name}: {len(articles)} items ({elapsed_ms}ms)")
    return articles, was_cached, "rss"


async def fetch_all(
  feeds_cfg: list[dict],
  max_age_hours: float = 25,
  max_items: int = 20,
) -> tuple[list[Article], list[tuple[int, bool, str | None]]]:
  sem = asyncio.Semaphore(_CONCURRENCY)
  for feed in feeds_cfg:
    feed.setdefault("max_items", max_items)
  async with httpx.AsyncClient(
    timeout=httpx.Timeout(60, connect=30),
    follow_redirects=True,
    limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
  ) as client:
    tasks = [
      _delayed_fetch(client, feed, random.uniform(0, 2), sem)
      for feed in feeds_cfg
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

  cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=max_age_hours)
  articles: list[Article] = []
  feed_results: list[tuple[int, bool, str | None]] = []
  seen_urls: set[str] = set()
  cached_feeds = 0
  fetched_feeds = 0
  error_feeds = 0
  skipped_web_feeds = 0
  total_items = 0
  total_fresh_items = 0
  for feed, result in zip(feeds_cfg, results):
    feed_id = feed.get("id")
    if isinstance(result, Exception):
      error_feeds += 1
      log.error(f"  [error] {feed.get('name')}: {type(result).__name__}: {result}")
      if feed_id is not None:
        feed_results.append((feed_id, False, str(result)))
      continue

    feed_articles, was_cached, kind = result
    if kind == "web-norules":
      skipped_web_feeds += 1
    elif was_cached:
      cached_feeds += 1
    else:
      fetched_feeds += 1
    total_items += len(feed_articles)
    if feed_id is not None:
      feed_results.append((feed_id, True, None))
    for a in feed_articles:
      if a.url in seen_urls:
        continue
      seen_urls.add(a.url)
      pub = a.published
      if pub is not None and pub.tzinfo is None:
        pub = pub.replace(tzinfo=timezone.utc)
        a.published = pub
      if pub is None or pub >= cutoff:
        articles.append(a)
        total_fresh_items += 1

  summary = (
    f"  Feeds: {fetched_feeds} fresh, {cached_feeds} cached"
    f"{f', {error_feeds} errors' if error_feeds else ''}"
    f"{f', {skipped_web_feeds} skipped (no rules)' if skipped_web_feeds else ''}"
    f" | items: {total_fresh_items} within window, {total_items} total."
  )
  log.info(summary)
  return articles, feed_results
