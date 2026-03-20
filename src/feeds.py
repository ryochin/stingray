import asyncio
import hashlib
import html
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import feedparser
import httpx


@dataclass
class Article:
  title: str
  url: str
  source: str
  published: datetime | None
  content_snippet: str
  title_ja: str = ""
  summary: str = ""

  def to_dict(self) -> dict:
    return {
      "title": self.title,
      "url": self.url,
      "source": self.source,
      "published": self.published.isoformat() if self.published else None,
      "content_snippet": self.content_snippet,
      "title_ja": self.title_ja,
      "summary": self.summary,
    }

  @classmethod
  def from_dict(cls, d: dict) -> "Article":
    published = None
    if d.get("published"):
      published = datetime.fromisoformat(d["published"])
    return cls(
      title=d["title"],
      url=d["url"],
      source=d["source"],
      published=published,
      content_snippet=d.get("content_snippet", ""),
      title_ja=d.get("title_ja", ""),
      summary=d.get("summary", ""),
    )


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
) -> tuple[str, bool]:
  """Fetch URL with L1 cache (ETag / content hash). Returns (body, was_cached)."""
  from cache import load_feed_cache, save_feed_cache

  cached = load_feed_cache(url)
  req_headers = dict(headers or {})
  if cached:
    if cached.get("etag"):
      req_headers["If-None-Match"] = cached["etag"]
    if cached.get("last_modified"):
      req_headers["If-Modified-Since"] = cached["last_modified"]

  try:
    resp = await client.get(url, headers=req_headers)
  except (httpx.ConnectError, httpx.TimeoutException, OSError) as e:
    if cached:
      print(f"    Network error, using cached feed: {e}")
      return cached["body"], True
    raise

  if resp.status_code == 304 and cached:
    return cached["body"], True

  resp.raise_for_status()
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
  parsed = feedparser.parse(body)

  articles = []
  for entry in parsed.entries[:max_items]:
    published = None
    if hasattr(entry, "published"):
      try:
        published = parsedate_to_datetime(entry.published)
      except Exception:
        pass

    snippet = ""
    if hasattr(entry, "summary"):
      snippet = _strip_html(entry.summary)
    elif hasattr(entry, "description"):
      snippet = _strip_html(entry.description)

    link = entry.get("link", "")
    if not link:
      continue

    articles.append(Article(
      title=entry.get("title", "(no title)"),
      url=link,
      source=source,
      published=published,
      content_snippet=_truncate(snippet),
    ))

  return articles


def _parse_reddit(body: str, feed_cfg: dict) -> list[Article]:
  import json
  max_items = feed_cfg.get("max_items", 15)
  data = json.loads(body)

  children = data.get("data", {}).get("children", [])
  articles = []
  for child in children[:max_items]:
    post = child["data"]
    if post.get("stickied"):
      continue

    permalink = post.get("permalink", "")
    if not permalink:
      continue

    published = None
    if post.get("created_utc"):
      published = datetime.fromtimestamp(post["created_utc"], tz=timezone.utc)

    snippet = _strip_html(post.get("selftext", ""))
    if not snippet:
      snippet = post.get("title", "")

    articles.append(Article(
      title=post["title"],
      url=f"https://reddit.com{permalink}",
      source=feed_cfg["name"],
      published=published,
      content_snippet=_truncate(snippet),
    ))

  return articles


REDDIT_HEADERS = {
  "User-Agent": "news-aggregator/0.1 (local feed reader)",
}


async def _fetch_rss(client: httpx.AsyncClient, feed_cfg: dict) -> tuple[list[Article], bool]:
  url = feed_cfg["url"]
  body, was_cached = await _fetch_with_cache(client, url)
  return _parse_rss(body, feed_cfg), was_cached


async def _fetch_reddit(client: httpx.AsyncClient, feed_cfg: dict) -> tuple[list[Article], bool]:
  subreddit = feed_cfg["subreddit"]
  sort = feed_cfg.get("sort", "hot")
  max_items = feed_cfg.get("max_items", 15)
  url = f"https://www.reddit.com/r/{subreddit}/{sort}.json?limit={max_items}"
  body, was_cached = await _fetch_with_cache(client, url, headers=REDDIT_HEADERS)
  return _parse_reddit(body, feed_cfg), was_cached


FETCHERS = {
  "rss": _fetch_rss,
  "reddit": _fetch_reddit,
}


async def fetch_all(feeds_cfg: list[dict], max_age_hours: float = 25) -> list[Article]:
  async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
    tasks = []
    task_feeds = []
    for feed in feeds_cfg:
      feed_type = feed.get("type", "rss")
      fetcher = FETCHERS.get(feed_type)
      if fetcher is None:
        print(f"  Unknown feed type: {feed_type}, skipping {feed.get('name')}")
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
      print(f"  Error: {task_feeds[i].get('name')}: {result}")
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
        if pub is None or pub >= cutoff:
          articles.append(a)

  print(f"  Feeds: {fetched_feeds} fetched, {cached_feeds} from cache.")
  return articles
