"""Tests for title normalization in `_parse_rss`.

Some upstream feeds embed HTML (notably `<br />`) inside <title>. The
ingest pipeline must strip those tags so downstream renderers don't show
literal `&lt;br /&gt;` to users.
"""

from __future__ import annotations

from feeds import _normalize_title, _parse_rss


def _feed_cfg() -> dict:
  return {"name": "Test", "url": "https://example.com/feed", "max_items": 50}


RSS_TITLE_WITH_BR = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>T</title>
    <link>https://example.com/</link>
    <item>
      <title><![CDATA[Foo<br />Bar]]></title>
      <link>https://example.com/a</link>
    </item>
  </channel>
</rss>
"""


RSS_TITLE_EMPTY = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>T</title>
    <link>https://example.com/</link>
    <item>
      <title></title>
      <link>https://example.com/a</link>
    </item>
  </channel>
</rss>
"""


def test_normalize_title_replaces_br_with_space():
  assert _normalize_title("Foo<br />Bar") == "Foo Bar"
  assert _normalize_title("Foo<br/>Bar") == "Foo Bar"
  assert _normalize_title("Foo<br>Bar") == "Foo Bar"
  assert _normalize_title("Foo<BR />Bar") == "Foo Bar"


def test_normalize_title_strips_other_tags():
  assert _normalize_title("Hello <b>world</b>") == "Hello world"


def test_normalize_title_unescapes_entities():
  assert _normalize_title("AT&amp;T") == "AT&T"


def test_normalize_title_collapses_whitespace():
  assert _normalize_title("  a   b\tc\n d  ") == "a b c d"


def test_normalize_title_handles_empty():
  assert _normalize_title("") == ""
  assert _normalize_title("   ") == ""


def test_parse_rss_strips_br_from_title():
  articles = _parse_rss(RSS_TITLE_WITH_BR, _feed_cfg())
  assert len(articles) == 1
  assert articles[0].title == "Foo Bar"


def test_parse_rss_falls_back_when_title_missing():
  articles = _parse_rss(RSS_TITLE_EMPTY, _feed_cfg())
  assert len(articles) == 1
  assert articles[0].title == "(no title)"
