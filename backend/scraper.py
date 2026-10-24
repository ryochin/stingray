"""Web page feed: CSS selector extraction for non-RSS pages."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from html import escape as html_escape
from typing import Any, cast
from urllib.parse import urljoin

import soupsieve
from bs4 import BeautifulSoup, Tag

from models import Article
from safe_http import SafeClient

# Extraction rule keys. The required three identify the repeating item and its
# title/link; the rest are optional refinements. "link" accepts the sentinel
# "_self" meaning the item element itself is the <a> (see parse_web_page).
ALLOWED_RULE_KEYS = frozenset(
  {
    "item",
    "title",
    "link",
    "link_attr",
    "date",
    "date_attr",
    "thumbnail",
    "thumbnail_attr",
  }
)
REQUIRED_RULE_KEYS = ("item", "title", "link")
SELECTOR_RULE_KEYS = ("item", "title", "link", "date", "thumbnail")
MAX_RULE_VALUE_LEN = 200


def validate_extraction_rules(raw: object) -> dict[str, str]:
  """Validate and normalize extraction rules. Raises ValueError on bad input.

  Shared by the PATCH /rules endpoint and LLM selector inference so the same
  contract (allowed keys, required fields, CSS syntax) is enforced in one place.
  """
  if not isinstance(raw, dict):
    raise ValueError("Extraction rules must be a JSON object")
  rules: dict[str, str] = {}
  # Non-str keys cannot match ALLOWED_RULE_KEYS and are rejected below.
  for key, value in cast(dict[str, object], raw).items():
    if key not in ALLOWED_RULE_KEYS:
      raise ValueError(f"Unknown extraction rule key: {key}")
    if value is None:
      # LLMs tend to fill optional fields with null instead of omitting them;
      # treat null as "omitted" (a missing required key is still caught below).
      continue
    if not isinstance(value, str):
      raise ValueError(f"Value for '{key}' must be a string")
    trimmed = value.strip()
    if len(trimmed) > MAX_RULE_VALUE_LEN:
      raise ValueError(f"Value for '{key}' is too long (max {MAX_RULE_VALUE_LEN} chars)")
    if trimmed:
      rules[key] = trimmed
  for key in REQUIRED_RULE_KEYS:
    if not rules.get(key):
      raise ValueError(f"Missing or empty required field: {key}")
  for key in SELECTOR_RULE_KEYS:
    selector = rules.get(key)
    if not selector or (key == "link" and selector == "_self"):
      continue
    try:
      soupsieve.compile(selector)
    except Exception as e:
      raise ValueError(f"Invalid CSS selector for '{key}': {selector!r}") from e
  return rules


def _parse_date(text: str | None) -> datetime | None:
  """Try to parse a date string into an aware datetime. Returns None on failure.

  Naive results are pinned to UTC: callers such as the manual single-feed
  fetch upsert them directly, and the DB would otherwise read them in the
  session's TimeZone.
  """
  if not text:
    return None
  text = text.strip()
  # ISO 8601
  try:
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
  except ValueError:
    pass
  # Common formats
  for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%B %d, %Y", "%d %b %Y", "%b %d, %Y"):
    try:
      return datetime.strptime(text, fmt).replace(tzinfo=UTC)
    except ValueError:
      continue
  # Japanese date: 2026年4月10日
  m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text)
  if m:
    try:
      return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=UTC)
    except ValueError:
      pass
  return None


def _extract_own_text(el: Tag, item_ids: set[int], max_len: int = 500) -> str:
  """Extract text from an element, stopping at any child that is itself an item.

  When html.parser fails to auto-close tags (e.g. <p>), subsequent items
  become nested children. This function avoids including their text.
  """
  parts: list[str] = []
  total = 0
  for child in el.children:
    if isinstance(child, Tag) and id(child) in item_ids:
      break
    # Strings too: get_text() returns their text, and "" for comments.
    text = child.get_text(" ", strip=True)
    if text:
      parts.append(text)
      total += len(text)
      if total > max_len:
        break
  return " ".join(parts)[:max_len]


def attr_str(el: Tag, name: str) -> str | None:
  """Read a single-valued attribute; None when missing or empty.

  bs4 returns a list for multi-valued attributes (`class`, `rel`, ...). Those
  are not usable as a URL or date, so they fail the page (as urljoin / strip
  did before) instead of silently picking one value.
  """
  value = el.get(name)
  if value is None or isinstance(value, str):
    return value or None
  if not value:
    return None
  raise TypeError(f"Attribute {name!r} is multi-valued: {value!r}")


def parse_web_page(
  html: str,
  rules: dict[str, str],
  feed_cfg: dict[str, Any],
  clean_url_fn: Callable[[str], str] | None = None,
) -> list[Article]:
  """Extract articles from HTML using CSS selector rules."""
  soup = BeautifulSoup(html, "html.parser")
  source: str = feed_cfg["name"]
  page_url: str = feed_cfg["url"]
  max_items: int = feed_cfg.get("max_items", 200)

  items = soup.select(rules["item"])
  item_ids = {id(el) for el in items}
  articles: list[Article] = []

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
    raw_url = attr_str(link_el, link_attr) or attr_str(link_el, "href")
    if not raw_url:
      continue
    article_url = urljoin(page_url, raw_url)
    if not article_url.startswith(("http://", "https://")):
      continue
    cleaned_url = clean_url_fn(article_url) if clean_url_fn else article_url

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
        date_text = attr_str(date_el, date_attr) if date_attr else None
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
        thumb_url = attr_str(thumb_el, thumb_attr) or attr_str(thumb_el, "src")
        if thumb_url:
          thumb_url = urljoin(page_url, thumb_url)
          thumbnail_html = f'<img src="{html_escape(thumb_url)}" alt="" />'

    articles.append(
      Article(
        title=title,
        url=cleaned_url,
        source=source,
        published=published,
        content_snippet=snippet,
        content_html=thumbnail_html,
      )
    )

  return articles


async def fetch_web_page(
  client: SafeClient,
  feed_cfg: dict[str, Any],
  clean_url_fn: Callable[[str], str] | None = None,
) -> tuple[list[Article], bool]:
  """Fetch a web page and extract articles using stored CSS rules.

  Returns (articles, was_cached). was_cached is always False for web pages.
  """
  url: str = feed_cfg["url"]
  rules_json: Any = feed_cfg.get("extraction_rules", "{}")
  rules: dict[str, str] = json.loads(rules_json) if isinstance(rules_json, str) else rules_json

  resp = await client.get(url)
  resp.raise_for_status()
  html = resp.text

  articles = parse_web_page(html, rules, feed_cfg, clean_url_fn=clean_url_fn)
  return articles, False
