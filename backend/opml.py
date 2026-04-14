"""OPML import/export for feed subscriptions."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from schemas import FeedRow, FolderRow


@dataclass
class ImportFeed:
  name: str
  type: str = "rss"
  url: str | None = None
  subreddit: str | None = None
  sort: str | None = None
  lang: str = "en"
  max_items: int = 20
  summarize: bool = True


@dataclass
class ImportFolder:
  name: str
  feeds: list[ImportFeed] = field(default_factory=list)


# -- Export --


def export_opml(folders: list[FolderRow], feeds: list[FeedRow]) -> str:
  root = ET.Element("opml", version="2.0")
  head = ET.SubElement(root, "head")
  ET.SubElement(head, "title").text = "News Reader Subscriptions"
  body = ET.SubElement(root, "body")

  feeds_by_folder: dict[int | None, list[FeedRow]] = {}
  for f in feeds:
    feeds_by_folder.setdefault(f.folder_id, []).append(f)

  sorted_folders = sorted(folders, key=lambda f: (f.position, f.id))
  for folder in sorted_folders:
    folder_feeds = feeds_by_folder.get(folder.id, [])
    if not folder_feeds:
      continue
    folder_el = ET.SubElement(body, "outline", text=folder.name, title=folder.name)
    for feed in folder_feeds:
      _add_feed_outline(folder_el, feed)

  for feed in feeds_by_folder.get(None, []):
    _add_feed_outline(body, feed)

  ET.indent(root, space="  ")
  return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


def _add_feed_outline(parent: ET.Element, feed: FeedRow) -> None:
  attrs: dict[str, str] = {"type": "rss", "text": feed.name, "title": feed.name}
  if feed.type == "reddit":
    attrs["xmlUrl"] = f"https://www.reddit.com/r/{feed.subreddit}/.rss"
    attrs["htmlUrl"] = f"https://www.reddit.com/r/{feed.subreddit}"
    if feed.subreddit:
      attrs["data-subreddit"] = feed.subreddit
    if feed.sort:
      attrs["data-sort"] = feed.sort
  else:
    if feed.url:
      attrs["xmlUrl"] = feed.url
  if feed.lang != "en":
    attrs["data-lang"] = feed.lang
  if feed.max_items != 20:
    attrs["data-max-items"] = str(feed.max_items)
  if not feed.summarize:
    attrs["data-summarize"] = "0"
  ET.SubElement(parent, "outline", **attrs)


# -- Import --

_REDDIT_URL_RE = re.compile(r"reddit\.com/r/([^/]+)")


def parse_opml(xml_content: str) -> tuple[list[ImportFolder], list[ImportFeed]]:
  """Parse OPML XML. Returns (folders_with_feeds, uncategorized_feeds)."""
  root = ET.fromstring(xml_content)
  body = root.find("body")
  if body is None:
    return [], []

  folders: list[ImportFolder] = []
  uncategorized: list[ImportFeed] = []

  for outline in body:
    xml_url = outline.get("xmlUrl")
    if xml_url:
      feed = _parse_feed_outline(outline)
      if feed:
        uncategorized.append(feed)
    else:
      folder = ImportFolder(name=outline.get("text") or outline.get("title") or "Unnamed")
      for child in outline:
        feed = _parse_feed_outline(child)
        if feed:
          folder.feeds.append(feed)
      if folder.feeds:
        folders.append(folder)

  return folders, uncategorized


def _parse_feed_outline(el: ET.Element) -> ImportFeed | None:
  xml_url = el.get("xmlUrl")
  if not xml_url:
    return None

  name = el.get("text") or el.get("title") or xml_url

  subreddit = el.get("data-subreddit")
  if not subreddit:
    m = _REDDIT_URL_RE.search(xml_url)
    if m:
      subreddit = m.group(1)

  feed_type = "reddit" if subreddit else "rss"
  lang = el.get("data-lang", "en")
  max_items_str = el.get("data-max-items")
  max_items = int(max_items_str) if max_items_str else 20
  summarize_str = el.get("data-summarize")
  summarize = summarize_str != "0" if summarize_str else True

  return ImportFeed(
    name=name,
    type=feed_type,
    url=xml_url if feed_type == "rss" else None,
    subreddit=subreddit,
    sort=el.get("data-sort"),
    lang=lang,
    max_items=max_items,
    summarize=summarize,
  )
