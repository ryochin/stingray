import asyncio
import hashlib
import html
import json
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
      log.warn(f"    Network error, using cached feed: {e}")
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

    snippet = ""
    if hasattr(entry, "summary"):
      snippet = _strip_html(entry.summary)
    elif hasattr(entry, "description"):
      snippet = _strip_html(entry.description)

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
      lang=_detect_lang(title, default_lang),
    ))

  return articles


def _parse_reddit(body: str, feed_cfg: dict) -> list[Article]:
  max_items = feed_cfg.get("max_items", 15)
  default_lang = feed_cfg.get("lang", "en")
  try:
    data = json.loads(body)
  except json.JSONDecodeError as e:
    log.error(f"    Invalid JSON from Reddit: {e}")
    return []
  if not isinstance(data, dict):
    log.error(f"    Unexpected Reddit response type: {type(data)}")
    return []

  children = (data.get("data") or {}).get("children", [])
  articles = []
  for child in children[:max_items]:
    post = child.get("data")
    if not post or post.get("stickied"):
      continue

    permalink = post.get("permalink", "")
    title = post.get("title", "")
    if not permalink or not title:
      continue

    published = None
    if post.get("created_utc"):
      published = datetime.fromtimestamp(post["created_utc"], tz=timezone.utc)

    snippet = _strip_html(post.get("selftext", ""))
    if not snippet:
      snippet = title

    articles.append(Article(
      title=title,
      url=f"https://reddit.com{permalink}",
      source=feed_cfg["name"],
      published=published,
      content_snippet=_truncate(snippet),
      lang=_detect_lang(title, default_lang),
    ))

  return articles


REDDIT_HEADERS = {
  "User-Agent": "news-aggregator/0.1 (local feed reader)",
}


async def _fetch_rss(client: httpx.AsyncClient, feed_cfg: dict) -> tuple[list[Article], bool]:
  url = feed_cfg["url"]
  body, was_cached = await _fetch_with_cache(client, url)
  return _parse_rss(body, feed_cfg), was_cached


_VALID_REDDIT_SORTS = {"hot", "new", "top", "rising"}


async def _fetch_reddit(client: httpx.AsyncClient, feed_cfg: dict) -> tuple[list[Article], bool]:
  subreddit = feed_cfg["subreddit"]
  if not re.fullmatch(r"[A-Za-z0-9_]{1,21}", subreddit):
    raise ValueError(f"Invalid subreddit name: {subreddit}")
  sort = feed_cfg.get("sort", "hot")
  if sort not in _VALID_REDDIT_SORTS:
    raise ValueError(f"Invalid Reddit sort: {sort}")
  max_items = feed_cfg.get("max_items", 15)
  url = f"https://www.reddit.com/r/{subreddit}/{sort}.json?limit={max_items}"
  body, was_cached = await _fetch_with_cache(client, url, headers=REDDIT_HEADERS)
  return _parse_reddit(body, feed_cfg), was_cached


FETCHERS = {
  "rss": _fetch_rss,
  "reddit": _fetch_reddit,
}


async def fetch_all(feeds_cfg: list[dict], max_age_hours: float = 25) -> list[Article]:  # type: ignore[type-arg]
  async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
    tasks = []
    task_feeds = []
    for feed in feeds_cfg:
      feed_type = feed.get("type", "rss")
      fetcher = FETCHERS.get(feed_type)
      if fetcher is None:
        log.warn(f"  Unknown feed type: {feed_type}, skipping {feed.get('name')}")
        continue
      tasks.append(fetcher(client, feed))
      task_feeds.append(feed)

    results = await asyncio.gather(*tasks, return_exceptions=True)

  cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=max_age_hours)
  articles = []
  seen_urls: set[str] = set()
  cached_feeds = 0
  fetched_feeds = 0
  for i, result in enumerate(results):
    if isinstance(result, Exception):
      log.error(f"  Error: {task_feeds[i].get('name')}: {result}")
    else:
      feed_articles, was_cached = result
      if was_cached:
        cached_feeds += 1
      else:
        fetched_feeds += 1
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
  return articles
