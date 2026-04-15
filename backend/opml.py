"""OPML import/export for feed subscriptions."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import urlparse

from schemas import FeedRow, FolderRow


@dataclass
class ImportFeed:
  name: str
  url: str
  site_url: str | None = None
  lang: str = "en"
  summarize: bool = False


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
  if feed.url:
    attrs["xmlUrl"] = feed.url
  if feed.site_url:
    attrs["htmlUrl"] = feed.site_url
  if feed.lang != "en":
    attrs["data-lang"] = feed.lang
  if feed.summarize:
    attrs["data-summarize"] = "1"
  ET.SubElement(parent, "outline", **attrs)


# -- Import --

_JA_KANA = re.compile(r"[\u3040-\u309F\u30A0-\u30FF]")


def _detect_lang(name: str, url: str | None = None) -> str:
  if _JA_KANA.search(name):
    return "ja"
  if url:
    try:
      host = urlparse(url).hostname or ""
      if host.endswith(".jp"):
        return "ja"
    except Exception:
      pass
  return "en"


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
  site_url = el.get("htmlUrl") or None
  lang = el.get("data-lang") or _detect_lang(name, xml_url)
  summarize_str = el.get("data-summarize")
  summarize = summarize_str == "1" if summarize_str else False

  return ImportFeed(
    name=name,
    url=xml_url,
    site_url=site_url,
    lang=lang,
    summarize=summarize,
  )
