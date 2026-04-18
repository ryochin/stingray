import asyncio
import hashlib
import html
import random
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import feedparser
import httpx

import log
from cache import load_feed_cache, save_feed_cache
from models import JA_KANA, Article


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
) -> tuple[str, bool]:
  """Fetch URL with L1 cache (ETag / content hash). Returns (body, was_cached)."""
  cached = load_feed_cache(url)
  if cached and "body" not in cached:
    cached = None
  req_headers = dict(headers or {})
  if cached:
    if cached.get("etag"):
      req_headers["If-None-Match"] = cached["etag"]
    if cached.get("last_modified"):
      req_headers["If-Modified-Since"] = cached["last_modified"]

  try:
    resp = await client.get(url, headers=req_headers)
  except (httpx.TransportError, OSError) as e:
    if cached:
      log.warn(f"    Network error, using cached feed: {url} ({type(e).__name__}: {e or 'no details'})")
      return cached["body"], True
    raise

  if resp.status_code == 304:
    if cached:
      return cached["body"], True
    log.warn(f"    304 without cached data, skipping: {url}")
    return "", True

  try:
    resp.raise_for_status()
  except httpx.HTTPStatusError as e:
    if e.response.status_code in {403, 429, 500, 502, 503, 504} and cached:
      log.warn(f"    HTTP {e.response.status_code}, using cached feed: {url}")
      return cached["body"], True
    raise
  body = resp.text

  if cached and hashlib.sha256(body.encode()).hexdigest() == cached.get("content_hash"):
    return body, True

  etag = resp.headers.get("etag")
  last_modified = resp.headers.get("last-modified")
  save_feed_cache(url, etag, last_modified, body)
  return body, False


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
  body, was_cached = await _fetch_with_cache(client, url)
  # Empty body happens on 304-without-cache (already logged in _fetch_with_cache).
  # Returning [] here lets callers treat it the same as "no new articles".
  if not body:
    return [], was_cached
  return _parse_rss(body, feed_cfg), was_cached


_CONCURRENCY = 5

async def _delayed_fetch(client, feed, delay: float, sem: asyncio.Semaphore):
  """Fetch a single feed with concurrency limit and random delay."""
  async with sem:
    if delay > 0:
      await asyncio.sleep(delay)
    rules_json = feed.get("extraction_rules")
    if rules_json and rules_json != "{}":
      import json as _json
      rules = _json.loads(rules_json) if isinstance(rules_json, str) else rules_json
      if rules.get("item"):
        from scraper import fetch_web_page
        return await fetch_web_page(client, feed)
      # Web feed with incomplete rules — skip
      return [], False
    if rules_json is not None:
      # Web feed with no rules yet — skip
      return [], False
    return await _fetch_rss(client, feed)


async def fetch_all(
  feeds_cfg: list[dict],
  max_age_hours: float = 25,
  max_items: int = 20,
) -> tuple[list[Article], list[tuple[int, bool, str | None]], list[int]]:
  sem = asyncio.Semaphore(_CONCURRENCY)
  for feed in feeds_cfg:
    feed.setdefault("max_items", max_items)
  async with httpx.AsyncClient(
    timeout=httpx.Timeout(60, connect=30),
    follow_redirects=True,
    limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
  ) as client:
    tasks = []
    task_feeds = []
    for feed in feeds_cfg:
      delay = random.uniform(0, 2)
      tasks.append(_delayed_fetch(client, feed, delay, sem))
      task_feeds.append(feed)

    results = await asyncio.gather(*tasks, return_exceptions=True)

  cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=max_age_hours)
  articles = []
  feed_results: list[tuple[int, bool, str | None]] = []
  stale_web_feeds: list[int] = []
  seen_urls: set[str] = set()
  cached_feeds = 0
  fetched_feeds = 0
  for i, result in enumerate(results):
    feed_id = task_feeds[i].get("id")
    is_web_feed = bool(task_feeds[i].get("extraction_rules"))
    if isinstance(result, Exception):
      log.error(f"  Error: {task_feeds[i].get('name')}: {result}")
      if feed_id is not None:
        feed_results.append((feed_id, False, str(result)))
    else:
      feed_articles, was_cached = result
      if was_cached:
        cached_feeds += 1
      else:
        fetched_feeds += 1
      if feed_id is not None:
        feed_results.append((feed_id, True, None))
      if is_web_feed and len(feed_articles) == 0 and feed_id is not None:
        stale_web_feeds.append(feed_id)
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

  log.info(f"  Feeds: {fetched_feeds} fetched, {cached_feeds} from cache.")
  return articles, feed_results, stale_web_feeds
