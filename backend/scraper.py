"""Web page feed: CSS selector extraction for non-RSS pages."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from models import Article


def _parse_date(text: str | None) -> datetime | None:
  """Try to parse a date string. Returns None on failure."""
  if not text:
    return None
  text = text.strip()
  # ISO 8601
  try:
    return datetime.fromisoformat(text)
  except ValueError:
    pass
  # Common formats
  for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%B %d, %Y", "%d %b %Y", "%b %d, %Y"):
    try:
      return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
    except ValueError:
      continue
  # Japanese date: 2026年4月10日
  m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text)
  if m:
    try:
      return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
    except ValueError:
      pass
  return None


def _extract_own_text(el, item_ids: set, max_len: int = 500) -> str:
  """Extract text from an element, stopping at any child that is itself an item.

  When html.parser fails to auto-close tags (e.g. <p>), subsequent items
  become nested children. This function avoids including their text.
  """
  parts = []
  total = 0
  for child in el.children:
    if hasattr(child, "name") and id(child) in item_ids:
      break
    if hasattr(child, "name"):
      text = child.get_text(" ", strip=True)
    else:
      text = str(child).strip()
    if text:
      parts.append(text)
      total += len(text)
      if total > max_len:
        break
  return " ".join(parts)[:max_len]


def parse_web_page(html: str, rules: dict, feed_cfg: dict) -> list[Article]:
  """Extract articles from HTML using CSS selector rules."""
  soup = BeautifulSoup(html, "html.parser")
  source = feed_cfg["name"]
  page_url = feed_cfg["url"]
  max_items = feed_cfg.get("max_items", 20)

  items = soup.select(rules["item"])
  item_ids = {id(el) for el in items}
  articles = []

  for el in items[:max_items]:
    # Title
    title_el = el.select_one(rules["title"])
    if not title_el:
      continue
    title = title_el.get_text(strip=True)
    if not title:
      continue

    # Link
    link_selector = rules.get("link", "")
    link_el = el.select_one(link_selector) if link_selector and link_selector != "_self" else None
    if not link_el:
      link_el = el  # item itself may be the <a> element
    link_attr = rules.get("link_attr", "href")
    raw_url = link_el.get(link_attr, "")
    if not raw_url:
      raw_url = link_el.get("href", "")
    if not raw_url:
      continue
    article_url = urljoin(page_url, raw_url)
    if not article_url.startswith(("http://", "https://")):
      continue

    # Extract text from direct children only, stopping at nested items
    # (html.parser may not auto-close tags like <p>, causing subsequent
    # items to nest inside the first one)
    snippet = _extract_own_text(el, item_ids)

    # Date (optional)
    published = None
    date_selector = rules.get("date")
    if date_selector:
      date_el = el.select_one(date_selector)
      if date_el:
        date_attr = rules.get("date_attr")
        date_text = date_el.get(date_attr) if date_attr else None
        if not date_text:
          date_text = date_el.get_text(strip=True)
        published = _parse_date(date_text)
    if not published:
      published = _parse_date(snippet[:200])

    # Thumbnail (optional)
    thumbnail_html = ""
    thumb_selector = rules.get("thumbnail")
    if thumb_selector:
      thumb_el = el.select_one(thumb_selector)
      if thumb_el:
        thumb_attr = rules.get("thumbnail_attr", "src")
        thumb_url = thumb_el.get(thumb_attr, "") or thumb_el.get("src", "")
        if thumb_url:
          thumb_url = urljoin(page_url, thumb_url)
          thumbnail_html = f'<img src="{thumb_url}" alt="" />'

    articles.append(Article(
      title=title,
      url=article_url,
      source=source,
      published=published,
      content_snippet=snippet,
      content_html=thumbnail_html,
    ))

  return articles


async def fetch_web_page(
  client: httpx.AsyncClient,
  feed_cfg: dict,
) -> tuple[list[Article], bool]:
  """Fetch a web page and extract articles using stored CSS rules.

  Returns (articles, was_cached). was_cached is always False for web pages.
  """
  url = feed_cfg["url"]
  rules_json = feed_cfg.get("extraction_rules", "{}")
  rules = json.loads(rules_json) if isinstance(rules_json, str) else rules_json

  resp = await client.get(url, follow_redirects=True)
  resp.raise_for_status()
  html = resp.text

  articles = parse_web_page(html, rules, feed_cfg)
  return articles, False
