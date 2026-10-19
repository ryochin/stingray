"""Attribute reading in parse_web_page: configured attrs, fallbacks, multi-valued attrs.

Multi-valued attributes (bs4 returns a list for e.g. `class` / `rel`) are not
supported as link / date / thumbnail sources; they fail the page as before
rather than silently picking one value.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from scraper import parse_web_page

FEED_CFG: dict[str, Any] = {"name": "Web", "url": "https://example.com/list/", "max_items": 50}


def _parse(item_html: str, **rules: str) -> list[Any]:
  html = f'<html><body><div class="item">{item_html}</div></body></html>'
  return parse_web_page(html, {"item": ".item", "title": ".t", "link": ".t", **rules}, FEED_CFG)


# --- link ---


def test_link_attr_is_used_and_joined() -> None:
  [article] = _parse('<a class="t" href="/h" data-u="/u">T</a>', link_attr="data-u")
  assert article.url == "https://example.com/u"


@pytest.mark.parametrize("attr", ["", 'data-u=""'])
def test_link_attr_missing_or_empty_falls_back_to_href(attr: str) -> None:
  [article] = _parse(f'<a class="t" href="rel/h" {attr}>T</a>', link_attr="data-u")
  assert article.url == "https://example.com/list/rel/h"


def test_item_without_any_link_is_skipped() -> None:
  assert _parse('<span class="t">T</span>') == []


def test_self_link_uses_item_element() -> None:
  html = '<html><body><a class="item" href="/self"><span class="t">T</span></a></body></html>'
  [article] = parse_web_page(html, {"item": ".item", "title": ".t", "link": "_self"}, FEED_CFG)
  assert article.url == "https://example.com/self"


# --- date ---


def test_date_attr_is_used() -> None:
  [article] = _parse(
    '<a class="t" href="/a">T</a><time datetime="2026-01-02">Jan 5, 2026</time>',
    date="time",
    date_attr="datetime",
  )
  assert article.published == datetime(2026, 1, 2, tzinfo=UTC)


@pytest.mark.parametrize("attr", ["", 'datetime=""'])
def test_date_attr_missing_or_empty_falls_back_to_text(attr: str) -> None:
  [article] = _parse(
    f'<a class="t" href="/a">T</a><time {attr}>2026-03-04</time>',
    date="time",
    date_attr="datetime",
  )
  assert article.published == datetime(2026, 3, 4, tzinfo=UTC)


def test_date_without_attr_uses_text() -> None:
  [article] = _parse('<a class="t" href="/a">T</a><time>2026/05/06</time>', date="time")
  assert article.published == datetime(2026, 5, 6, tzinfo=UTC)


# --- thumbnail ---


def test_thumbnail_attr_is_used_and_joined() -> None:
  [article] = _parse(
    '<a class="t" href="/a">T</a><img src="/s.png" data-src="/d.png">',
    thumbnail="img",
    thumbnail_attr="data-src",
  )
  assert article.content_html == '<img src="https://example.com/d.png" alt="" />'


@pytest.mark.parametrize("attr", ["", 'data-src=""'])
def test_thumbnail_attr_missing_or_empty_falls_back_to_src(attr: str) -> None:
  [article] = _parse(
    f'<a class="t" href="/a">T</a><img src="s.png" {attr}>',
    thumbnail="img",
    thumbnail_attr="data-src",
  )
  assert article.content_html == '<img src="https://example.com/list/s.png" alt="" />'


def test_thumbnail_without_any_source_is_empty() -> None:
  [article] = _parse('<a class="t" href="/a">T</a><img>', thumbnail="img")
  assert article.content_html == ""


# --- multi-valued attributes ---


def test_empty_multi_valued_attrs_fall_back() -> None:
  # bs4 parses class="" as an empty list; it must fall back like a missing attr.
  [article] = _parse(
    '<a class="t" href="/h" rel="">T</a><time class="">2026-03-04</time><img class="" src="/s.png">',
    link_attr="rel",
    date="time",
    date_attr="class",
    thumbnail="img",
    thumbnail_attr="class",
  )
  assert article.url == "https://example.com/h"
  assert article.published == datetime(2026, 3, 4, tzinfo=UTC)
  assert article.content_html == '<img src="https://example.com/s.png" alt="" />'


@pytest.mark.parametrize(
  ("item_html", "rules"),
  [
    ('<a class="t" href="/a">T</a>', {"link_attr": "class"}),
    (
      '<a class="t" href="/a">T</a><time class="d">2026-01-01</time>',
      {"date": "time", "date_attr": "class"},
    ),
    (
      '<a class="t" href="/a">T</a><img class="i" src="/s.png">',
      {"thumbnail": "img", "thumbnail_attr": "class"},
    ),
  ],
)
def test_multi_valued_attr_raises(item_html: str, rules: dict[str, str]) -> None:
  with pytest.raises(TypeError, match="multi-valued"):
    _parse(item_html, **rules)
