"""Feed bodies come from untrusted servers: parsing one must never make the
parsers read files, fetch URLs, or expand XML entities."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import feedparser.http  # pyright: ignore[reportMissingTypeStubs]
import pytest

import feeds
from scraper import parse_web_page

RSS: str = (
  '<?xml version="1.0"?>'
  '<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel>'
  "<title>T</title><link>https://example.com/</link>"
  "<item><title>a</title><link>https://example.com/a</link>"
  '<media:thumbnail url="https://example.com/a.jpg"/></item>'
  "</channel></rss>"
)


def test_a_body_that_names_a_file_is_not_opened(tmp_path: Path):
  feed_file: Path = tmp_path / "feed.xml"
  feed_file.write_text(RSS, encoding="utf-8")
  title, _site, _lang, has_entries = feeds.probe_feed_body(str(feed_file))
  assert (title, has_entries) == (None, False)


def test_a_body_that_looks_like_a_url_is_not_fetched(monkeypatch: pytest.MonkeyPatch):
  calls: list[Any] = []

  def fake_get(*a: Any, **_kw: Any) -> bytes:
    calls.append(a)
    return b""

  monkeypatch.setattr(feedparser.http, "get", fake_get)
  feeds.extract_site_url("http://169.254.169.254/latest/meta-data/")
  assert calls == []


def test_item_images_are_still_extracted():
  assert feeds._extract_item_images(RSS) == {"https://example.com/a": "https://example.com/a.jpg"}


def test_entity_declarations_yield_no_images():
  body = RSS.replace(
    '<?xml version="1.0"?>',
    '<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY img "https://example.com/a.jpg">]>',
  ).replace('url="https://example.com/a.jpg"', 'url="&img;"')
  assert feeds._extract_item_images(body) == {}


@pytest.mark.parametrize("href", ["javascript:alert(1)", "mailto:a@example.com"])
def test_scraped_items_linking_to_non_http_schemes_are_dropped(href: str) -> None:
  # Article URLs are rendered as links, so only http(s) may reach the reader.
  html = (
    f'<div class="item"><a class="t" href="{href}">Bad</a></div>'
    '<div class="item"><a class="t" href="/ok">Good</a></div>'
  )
  rules = {"item": ".item", "title": ".t", "link": ".t"}
  feed_cfg = {"name": "Web", "url": "https://example.com/", "max_items": 10}
  assert [a.url for a in parse_web_page(html, rules, feed_cfg)] == ["https://example.com/ok"]


def test_scraped_thumbnail_url_is_escaped():
  html = (
    '<div class="item"><a class="t" href="https://example.com/1">One</a>'
    '<img src="https://example.com/x.jpg&quot; onerror=&quot;alert(1)"></div>'
  )
  rules = {"item": ".item", "title": ".t", "link": ".t", "thumbnail": "img"}
  feed_cfg = {"name": "Web", "url": "https://example.com/", "max_items": 10}
  [article] = parse_web_page(html, rules, feed_cfg)
  assert '" onerror="' not in article.content_html
  assert "&quot; onerror=&quot;" in article.content_html
