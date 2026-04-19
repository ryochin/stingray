"""OPML import/export for feed subscriptions."""

from __future__ import annotations

import xml.etree.ElementTree as ET
import defusedxml.ElementTree as SafeET
from dataclasses import dataclass, field

import lang
from schemas import FeedRow, FolderRow


@dataclass
class ImportFeed:
  name: str
  url: str
  site_url: str | None = None
  translate: bool = False
  summarize: bool = False
  extraction_rules: str | None = None


@dataclass
class ImportFolder:
  name: str
  feeds: list[ImportFeed] = field(default_factory=list["ImportFeed"])


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
  is_web = feed.extraction_rules is not None
  attrs: dict[str, str] = {
    "type": "web" if is_web else "rss",
    "text": feed.name,
    "title": feed.name,
  }
  if feed.url:
    if is_web:
      attrs["htmlUrl"] = feed.url
    else:
      attrs["xmlUrl"] = feed.url
  if feed.site_url:
    attrs["htmlUrl"] = feed.site_url
  if feed.translate:
    attrs["data-translate"] = "1"
  if feed.summarize:
    attrs["data-summarize"] = "1"
  if is_web and feed.extraction_rules and feed.extraction_rules != "{}":
    attrs["data-extraction-rules"] = feed.extraction_rules
  ET.SubElement(parent, "outline", attrib=attrs)


# -- Import --


def _should_translate(name: str, url: str | None, native_lang: str) -> bool:
  """Guess if a feed needs translation based on name and URL."""
  source_lang = lang.detect_lang_by_script(name) or lang.detect_lang_by_tld(url)
  return lang.should_translate(source_lang, native_lang)


def parse_opml(
  xml_content: str,
  native_lang: str = "ja",
) -> tuple[list[ImportFolder], list[ImportFeed]]:
  """Parse OPML XML. Returns (folders_with_feeds, uncategorized_feeds)."""
  root = SafeET.fromstring(xml_content)
  body = root.find("body")
  if body is None:
    return [], []

  folders: list[ImportFolder] = []
  uncategorized: list[ImportFeed] = []

  for outline in body:
    xml_url = outline.get("xmlUrl")
    if xml_url:
      feed = _parse_feed_outline(outline, native_lang)
      if feed:
        uncategorized.append(feed)
    else:
      folder = ImportFolder(name=outline.get("text") or outline.get("title") or "Unnamed")
      for child in outline:
        feed = _parse_feed_outline(child, native_lang)
        if feed:
          folder.feeds.append(feed)
      if folder.feeds:
        folders.append(folder)

  return folders, uncategorized


def _parse_feed_outline(el: ET.Element, native_lang: str) -> ImportFeed | None:
  feed_type = el.get("type", "rss")
  is_web = feed_type == "web"
  xml_url = el.get("xmlUrl")
  html_url = el.get("htmlUrl")

  url = xml_url or (html_url if is_web else None)
  if not url:
    return None

  name = el.get("text") or el.get("title") or url
  site_url = html_url if not is_web else None
  translate_str = el.get("data-translate")
  translate = translate_str == "1" if translate_str else _should_translate(name, url, native_lang)
  summarize_str = el.get("data-summarize")
  summarize = summarize_str == "1" if summarize_str else False
  extraction_rules = el.get("data-extraction-rules") if is_web else None

  return ImportFeed(
    name=name,
    url=url,
    site_url=site_url,
    translate=translate,
    summarize=summarize,
    extraction_rules=extraction_rules or ("{}" if is_web else None),
  )
