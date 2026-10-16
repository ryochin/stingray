import asyncio
import hashlib
import html
import io
import json
import random
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import defusedxml
import defusedxml.ElementTree as SafeET
import feedparser  # pyright: ignore[reportMissingTypeStubs]
import httpx
from bs4 import BeautifulSoup

import lang
import log
from cache import load_feed_cache, save_feed_cache
from html_urls import absolutize_html_urls
from models import Article
from safe_http import FetchSettings, SafeClient
from scraper import attr_str, fetch_web_page

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

  Relative paths (`file://name.xml`, `file://./name.xml`) resolve against
  DEBUG_FEEDS_DIR; absolute paths (`file:///abs/path`) are taken as-is. Either
  way the resolved path must stay inside DEBUG_FEEDS_DIR, otherwise
  ValueError: feed URLs come from an unauthenticated endpoint, so this must
  not become an arbitrary file read.
  """
  raw = url.removeprefix("file://")
  base = DEBUG_FEEDS_DIR.resolve()
  # Joining an absolute path replaces the base, so one expression covers both
  # forms. resolve() collapses `..` and follows symlinks before the check.
  path = (base / raw).resolve()
  if not path.is_relative_to(base):
    raise ValueError(f"file:// path outside {DEBUG_FEEDS_DIR}: {raw}")
  return path.read_text(encoding="utf-8")


def _is_alternate(rel: str | None) -> bool:
  return rel is not None and "alternate" in rel


def extract_feed_candidates(body: str, base_url: str) -> list[dict[str, str]]:
  """Extract feed candidates from an HTML page via <link rel="alternate">.

  Returns a list of {href, title, type} dicts with duplicates removed.
  The href is resolved against base_url so callers receive absolute URLs.
  """
  soup = BeautifulSoup(body, "html.parser")
  seen: set[str] = set()
  candidates: list[dict[str, str]] = []
  for type_hint in _FEED_LINK_TYPES:
    for link in soup.find_all("link", rel=_is_alternate):
      link_type = (attr_str(link, "type") or "").strip().lower()
      if link_type != type_hint:
        continue
      href = (attr_str(link, "href") or "").strip()
      if not href:
        continue
      absolute = urljoin(base_url, href)
      if absolute in seen:
        continue
      seen.add(absolute)
      candidates.append({
        "href": absolute,
        "title": (attr_str(link, "title") or "").strip(),
        "type": link_type,
      })
  return candidates


# ---------------------------------------------------------------------------
# Typed wrappers around feedparser for callers that are pyright-strict.
# ---------------------------------------------------------------------------


def _parse_feed(body: str) -> Any:
  """feedparser.parse() on a body that is always treated as content.

  feedparser is untyped, so the result is Any: this is the boundary where its
  Unknown types stop. Read string fields through _str_field.

  Given a str, feedparser fetches it if it looks like a URL and opens it if it
  names a readable file — an SSRF / local-file read for a body served by a
  hostile feed. A byte stream is only ever read. The UTF-8 encoding matches
  what feedparser does to a plain str body, so encoding detection is unchanged.
  """
  return feedparser.parse(io.BytesIO(body.encode("utf-8")))  # pyright: ignore[reportUnknownMemberType]


def _str_field(d: Any, key: str) -> str:
  """Read a string field from a FeedParserDict; "" when missing or not a str.

  Uses .get() so FeedParserDict's key aliases (e.g. description -> summary)
  still apply.
  """
  value = d.get(key)
  return value if isinstance(value, str) else ""


def probe_feed_body(body: str) -> tuple[str | None, str | None, str | None, bool]:
  """Parse a feed body and return (title, site_url, detected_lang, has_entries).

  Attempts feed-level language detection using:
    - Feed `language` attribute
    - Japanese kana in the first few entry titles
  Returns detected_lang=None if none of the heuristics succeed.
  """
  parsed = _parse_feed(body)
  if not parsed.entries:
    return None, None, None, False

  feed_meta: Any = parsed.feed or {}
  title = _str_field(feed_meta, "title").strip() or None
  site_url = _str_field(feed_meta, "link").strip() or None

  feed_lang = _str_field(feed_meta, "language")
  detected_lang = lang.normalize_lang_code(feed_lang)

  if detected_lang is None:
    for entry in parsed.entries[:5]:
      entry_title = _str_field(entry, "title")
      detected_lang = lang.detect_lang_by_script(entry_title)
      if detected_lang is not None:
        break

  return title, site_url, detected_lang, True


def extract_site_url(body: str) -> str | None:
  """Return the <link> of a feed body, or None if parsing fails."""
  parsed = _parse_feed(body)
  return _str_field(parsed.feed or {}, "link").strip() or None


def _normalize_title(text: str) -> str:
  # Some upstream feeds embed HTML (e.g. <br />) inside <title>. Replace
  # line-break tags with a space so the surrounding words don't fuse, then
  # drop any remaining tags and collapse whitespace.
  text = html.unescape(text)
  text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
  text = re.sub(r"<[^>]+>", "", text)
  text = re.sub(r"\s+", " ", text)
  return text.strip()


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
  client: SafeClient,
  url: str,
  headers: dict[str, str] | None = None,
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
    body = await asyncio.to_thread(read_file_url, url)
    log.dim(f"    FILE {url} → {len(body.encode('utf-8')) / 1024:.1f}KB")
    return body, False, "file"

  # Cache bodies run to hundreds of KB and every feed fetches concurrently, so
  # reading or writing them on the event loop serialises the whole refresh.
  cached = await asyncio.to_thread(load_feed_cache, url)
  cached_body = cached.get("body") if cached else None
  if not isinstance(cached_body, str):
    cached, cached_body = None, None
  req_headers = dict(headers or {})
  conditional = False
  if cached:
    etag = cached.get("etag")
    if isinstance(etag, str) and etag:
      req_headers["If-None-Match"] = etag
      conditional = True
    last_modified = cached.get("last_modified")
    if isinstance(last_modified, str) and last_modified:
      req_headers["If-Modified-Since"] = last_modified
      conditional = True

  start = time.perf_counter()
  try:
    resp = await client.get(url, headers=req_headers)
  except (httpx.TransportError, OSError) as e:
    if cached_body is not None:
      log.warn(f"    Network error, using cached feed: {url} ({type(e).__name__}: {e or 'no details'})")
      return cached_body, True, "net-cache"
    raise
  elapsed_ms = int((time.perf_counter() - start) * 1000)

  if resp.status_code == 304:
    if cached_body is not None:
      log.dim(f"    GET {url} → 304 Not Modified ({elapsed_ms}ms, cache hit)")
      return cached_body, True, "304"
    log.warn(f"    304 without cached data, skipping: {url}")
    return "", True, "304-empty"

  try:
    resp.raise_for_status()
  except httpx.HTTPStatusError as e:
    if e.response.status_code in {403, 429, 500, 502, 503, 504} and cached_body is not None:
      log.warn(f"    HTTP {e.response.status_code}, using cached feed: {url}")
      return cached_body, True, "5xx-cache"
    raise
  body = resp.text
  size_kb = len(body.encode("utf-8")) / 1024

  if cached and hashlib.sha256(body.encode()).hexdigest() == cached.get("content_hash"):
    log.dim(f"    GET {url} → {resp.status_code} {size_kb:.1f}KB ({elapsed_ms}ms, unchanged)")
    return body, True, "unchanged"

  etag = resp.headers.get("etag")
  last_modified = resp.headers.get("last-modified")
  await asyncio.to_thread(save_feed_cache, url, etag, last_modified, body)
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
    root = SafeET.fromstring(body)
  except (ET.ParseError, defusedxml.DefusedXmlException):
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
  feed_cfg: dict[str, Any],
  clean_url_fn: Callable[[str], str] | None = None,
) -> list[Article]:
  max_items: int = feed_cfg.get("max_items", 200)
  source: str = feed_cfg["name"]
  parsed = _parse_feed(body)
  item_images = _extract_item_images(body)

  articles: list[Article] = []
  entries: list[Any] = parsed.entries
  for entry in entries[:max_items]:
    published = None
    for attr in ("published_parsed", "updated_parsed"):
      if hasattr(entry, attr) and getattr(entry, attr):
        try:
          published = datetime(*getattr(entry, attr)[:6], tzinfo=UTC)
          break
        except (ValueError, TypeError):
          pass

    raw_html = ""
    snippet = ""
    # Prefer content:encoded (full article) over description (often truncated)
    contents = entry.get("content")
    if contents:
      raw_html = _str_field(contents[0], "value")
    if not raw_html:
      raw_html = _str_field(entry, "summary")
    if not raw_html:
      raw_html = _str_field(entry, "description")
    if raw_html:
      snippet = _strip_html(raw_html)

    raw_link = _str_field(entry, "link")
    if not raw_link or not raw_link.startswith(("http://", "https://")):
      continue
    cleaned_link = clean_url_fn(raw_link) if clean_url_fn else raw_link

    # Inject thumbnail image into content_html if not already present.
    # `item_images` was populated from the same raw <link>, so lookup
    # uses the raw value — never the cleaned one.
    thumb_url = item_images.get(raw_link)
    if thumb_url and thumb_url not in raw_html:
      raw_html = f'<img src="{html.escape(thumb_url)}" alt="" />\n{raw_html}'

    # Resolve relative URLs against the item's own link — never the feed URL.
    # Generated mirror feeds (e.g. a machi.to thread republished elsewhere)
    # host the XML on a different origin than the articles it points at, so the
    # feed URL would absolutize links onto the wrong site. Runs after the
    # thumbnail injection so relative `item_images` URLs are covered too.
    raw_html = absolutize_html_urls(raw_html, raw_link)

    title = _normalize_title(_str_field(entry, "title")) or "(no title)"
    articles.append(Article(
      title=title,
      url=cleaned_link,
      source=source,
      published=published,
      content_snippet=_truncate(snippet),
      content_html=raw_html,
    ))

  return articles


async def fetch_rss(
  client: SafeClient,
  feed_cfg: dict[str, Any],
  clean_url_fn: Callable[[str], str] | None = None,
) -> tuple[list[Article], bool, str]:
  """Fetch an RSS feed body with caching and parse it.

  Returns (articles, was_cached, source_tag). The source_tag is forwarded
  from _fetch_with_cache so schedulers downstream can distinguish a normal
  fetch from a degraded cache fallback.
  """
  url: str = feed_cfg["url"]
  body, was_cached, tag = await _fetch_with_cache(client, url)
  # Empty body happens on 304-without-cache (already logged in _fetch_with_cache).
  # Returning [] here lets callers treat it the same as "no new articles".
  if not body:
    return [], was_cached, tag
  return _parse_rss(body, feed_cfg, clean_url_fn=clean_url_fn), was_cached, tag


_CONCURRENCY = 5

async def _delayed_fetch(
  client: SafeClient,
  feed: dict[str, Any],
  delay: float,
  sem: asyncio.Semaphore,
  clean_url_fn: Callable[[str], str] | None = None,
) -> tuple[list[Article], bool, str, str | None]:
  """Fetch a single feed with concurrency limit and random delay.

  Returns (articles, was_cached, kind, source_tag) where
    - `kind` is a short label indicating how the feed was fetched
      ("rss" / "web" / "web-norules") — used for per-feed summary logging.
    - `source_tag` is the low-level fetch tag from _fetch_with_cache for RSS
      feeds (e.g. "fresh", "unchanged", "304", "304-empty", "net-cache",
      "5xx-cache"); None for web feeds (they don't go through the cache layer).
  """
  name: str = feed.get("name") or feed.get("url") or "?"
  async with sem:
    if delay > 0:
      await asyncio.sleep(delay)
    rules_json: Any = feed.get("extraction_rules")
    is_web = rules_json is not None
    if is_web and rules_json != "{}":
      rules: dict[str, Any] = json.loads(rules_json) if isinstance(rules_json, str) else rules_json
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
    articles, was_cached, source_tag = await fetch_rss(
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
      pub = pub.replace(tzinfo=UTC)
      a.published = pub
    if pub is None or pub >= cutoff:
      fresh.append(a)
  return fresh


OnFeedDone = Callable[
  [dict[str, Any], list[Article], str | None, str, Exception | None],
  Awaitable[None],
]


async def fetch_all(
  feeds_cfg: list[dict[str, Any]],
  max_age_hours: float = 48,
  max_items: int = 200,
  on_feed_done: OnFeedDone | None = None,
  clean_url_fn: Callable[[str], str] | None = None,
  *,
  fetch_settings: FetchSettings,
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
  cutoff = datetime.now(tz=UTC) - timedelta(hours=max_age_hours)

  async def _run_one(
    client: SafeClient, feed: dict[str, Any],
  ) -> tuple[list[Article], bool, str, Exception | None]:
    try:
      articles, was_cached, kind, source_tag = await _delayed_fetch(
        client, feed, random.uniform(0, 2), sem,
        clean_url_fn=clean_url_fn,
      )
      fetch_exc: Exception | None = None
    except Exception as e:
      log.error(f"  [error] {feed.get('name')}: {type(e).__name__}: {e}")
      articles: list[Article] = []
      was_cached, kind, source_tag = False, "rss", None
      fetch_exc = e

    fresh = _filter_by_cutoff(articles, cutoff) if articles else []

    if on_feed_done is not None:
      try:
        await on_feed_done(feed, fresh, source_tag, kind, fetch_exc)
      except Exception as e:
        log.error(f"  [persist-error] {feed.get('name')}: {type(e).__name__}: {e}")

    return fresh, was_cached, kind, fetch_exc

  async with SafeClient(
    fetch_settings,
    timeout=httpx.Timeout(60, connect=30),
    limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
  ) as client:
    tasks = [_run_one(client, feed) for feed in feeds_cfg]
    results = await asyncio.gather(*tasks)

  return _aggregate_feed_results(feeds_cfg, results)


def _aggregate_feed_results(
  feeds_cfg: list[dict[str, Any]],
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
  for _feed, (feed_articles, was_cached, kind, fetch_exc) in zip(feeds_cfg, results, strict=True):
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
