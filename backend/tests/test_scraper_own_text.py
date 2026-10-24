"""Snippet text in parse_web_page: _extract_own_text joins an item's own text.

html.parser does not auto-close tags like <p>, so later items can end up nested
inside an earlier one. The snippet must stop at the first nested item.
"""

from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup, Tag

from scraper import _extract_own_text, parse_web_page

FEED_CFG: dict[str, Any] = {"name": "Web", "url": "https://example.com/list/", "max_items": 50}


def _own_text(item_html: str, max_len: int = 500) -> str:
  soup = BeautifulSoup(f'<div class="item">{item_html}</div>', "html.parser")
  el = soup.select_one(".item")
  assert isinstance(el, Tag)
  return _extract_own_text(el, {id(el)}, max_len)


def test_joins_strings_and_tags_with_spaces() -> None:
  assert _own_text("Lead <b>bold</b><i>italic</i> tail") == "Lead bold italic tail"


def test_tag_text_is_joined_from_its_descendants() -> None:
  assert _own_text("<span>a<em>b</em></span>") == "a b"


def test_comments_are_left_out() -> None:
  assert _own_text("before<!-- hidden -->after") == "before after"


def test_whitespace_only_children_are_skipped() -> None:
  assert _own_text("a \n <span> </span>  <b>b</b>") == "a b"


def test_cuts_at_max_len() -> None:
  assert _own_text("abcdef <b>ghij</b>", max_len=8) == "abcdef g"


def test_stops_at_a_nested_item() -> None:
  html = '<div class="list"><p class="item">One<p class="item">Two</div>'
  soup = BeautifulSoup(html, "html.parser")
  first, second = soup.select(".item")
  assert second.parent is first  # html.parser nested the second item
  assert _extract_own_text(first, {id(first), id(second)}) == "One"


def test_nested_child_that_is_not_an_item_is_read() -> None:
  html = '<div class="list"><p class="item">One<p class="other">Two</div>'
  soup = BeautifulSoup(html, "html.parser")
  first = soup.select_one(".item")
  assert isinstance(first, Tag)
  assert _extract_own_text(first, {id(first)}) == "One Two"


def test_parse_web_page_snippets_exclude_nested_items() -> None:
  html = (
    "<html><body>"
    '<p class="item"><a class="t" href="/1">First</a> body one'
    '<p class="item"><a class="t" href="/2">Second</a> body two'
    "</body></html>"
  )
  articles = parse_web_page(html, {"item": ".item", "title": ".t", "link": ".t"}, FEED_CFG)
  assert [(a.title, a.content_snippet) for a in articles] == [
    ("First", "First body one"),
    ("Second", "Second body two"),
  ]
