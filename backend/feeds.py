import asyncio
import hashlib
import html
import random
import re
from datetime import datetime, timedelta, timezone

import feedparser
import httpx

import log
from cache import load_feed_cache, save_feed_cache
from models import Article


def _strip_html(text: str) -> str:
  text = html.unescape(text)
  text = re.sub(r"<[^>]+>", "", text)
  text = re.sub(r"(Article|Comments) URL: https?://\S+\n?", "", text)
  text = re.sub(r"Points: \d+\n?", "", text)
  text = re.sub(r"# Comments: \d+\n?", "", text)
  return text.strip()


# Hiragana or katakana in the title is a strong signal that the article is Japanese.
# Kanji alone is ambiguous (Chinese shares them), so we deliberately exclude the CJK range.
_JA_KANA = re.compile(r"[\u3040-\u309F\u30A0-\u30FF]")


def _detect_lang(title: str, default: str) -> str:
  if title and _JA_KANA.search(title):
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


def _parse_rss(body: str, feed_cfg: dict) -> list[Article]:
  max_items = feed_cfg.get("max_items", 20)
  source = feed_cfg["name"]
  default_lang = feed_cfg.get("lang", "en")
  parsed = feedparser.parse(body)

  articles = []
  for entry in parsed.entries[:max_items]:
    published = None
    if hasattr(entry, "published_parsed") and entry.published_parsed:
      try:
        published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
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

    title = entry.get("title", "(no title)")
    articles.append(Article(
      title=title,
      url=link,
      source=source,
      published=published,
      content_snippet=_truncate(snippet),
      content_html=raw_html,
      lang=_detect_lang(title, default_lang),
    ))

  return articles


async def _fetch_rss(client: httpx.AsyncClient, feed_cfg: dict) -> tuple[list[Article], bool]:
  url = feed_cfg["url"]
  body, was_cached = await _fetch_with_cache(client, url)
  return _parse_rss(body, feed_cfg), was_cached


_CONCURRENCY = 5

async def _delayed_fetch(client, feed, delay: float, sem: asyncio.Semaphore):
  """Fetch a single feed with concurrency limit and random delay."""
  async with sem:
    if delay > 0:
      await asyncio.sleep(delay)
    return await _fetch_rss(client, feed)


async def fetch_all(
  feeds_cfg: list[dict],
  max_age_hours: float = 25,
  max_items: int = 20,
) -> tuple[list[Article], list[tuple[int, bool, str | None]]]:
  sem = asyncio.Semaphore(_CONCURRENCY)
  for feed in feeds_cfg:
    feed.setdefault("max_items", max_items)
  async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
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
  seen_urls: set[str] = set()
  cached_feeds = 0
  fetched_feeds = 0
  for i, result in enumerate(results):
    feed_id = task_feeds[i].get("id")
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
  return articles, feed_results
