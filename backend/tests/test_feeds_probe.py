"""Tests for feeds.probe_feed_body / extract_site_url.

These touch feedparser — the key invariants are:
  - RSS/Atom both yield a parsed feed
  - language is read from <language> or inferred from kana in first 5 entries
  - normalize_lang_code strips BCP47 region (en-US → en)
  - malformed / empty bodies return all-None + has_entries=False
"""

from __future__ import annotations

from feeds import extract_feed_candidates, extract_site_url, probe_feed_body


RSS_JA = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>日刊ニュース</title>
  <link>https://example.jp/</link>
  <language>ja</language>
  <item><title>今日の記事</title><link>https://example.jp/1</link></item>
</channel></rss>
"""

RSS_EN_US = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Daily News</title>
  <link>https://example.com/</link>
  <language>en-US</language>
  <item><title>Article one</title><link>https://example.com/1</link></item>
</channel></rss>
"""

# No <language> attribute; kana in an entry title should drive detection.
RSS_KANA_INFERRED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Untagged</title>
  <link>https://example.com/</link>
  <item><title>English only</title><link>https://example.com/1</link></item>
  <item><title>カタカナ記事</title><link>https://example.com/2</link></item>
</channel></rss>
"""

# No language hints anywhere.
RSS_NO_LANG = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Untagged</title>
  <link>https://example.com/</link>
  <item><title>Article one</title><link>https://example.com/1</link></item>
</channel></rss>
"""

ATOM_JA = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="ja">
  <title>はてブ</title>
  <link href="https://b.hatena.ne.jp/"/>
  <entry><title>エントリ</title><link href="https://b.hatena.ne.jp/1"/></entry>
</feed>
"""

EMPTY_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Empty</title><link>https://example.com/</link>
</channel></rss>
"""


class TestProbeFeedBody:
  def test_ja_rss_extracts_all_fields(self):
    title, site_url, lang_code, has_entries = probe_feed_body(RSS_JA)
    assert title == "日刊ニュース"
    assert site_url == "https://example.jp/"
    assert lang_code == "ja"
    assert has_entries is True

  def test_en_us_normalized_to_en(self):
    _, _, lang_code, _ = probe_feed_body(RSS_EN_US)
    assert lang_code == "en"

  def test_language_inferred_from_kana_in_entry(self):
    _, _, lang_code, has_entries = probe_feed_body(RSS_KANA_INFERRED)
    assert lang_code == "ja"
    assert has_entries is True

  def test_no_language_hints_returns_none(self):
    _, _, lang_code, _ = probe_feed_body(RSS_NO_LANG)
    assert lang_code is None

  def test_atom_ja(self):
    title, site_url, lang_code, has_entries = probe_feed_body(ATOM_JA)
    assert title == "はてブ"
    assert site_url == "https://b.hatena.ne.jp/"
    assert lang_code == "ja"
    assert has_entries is True

  def test_empty_feed_returns_all_none(self):
    title, site_url, lang_code, has_entries = probe_feed_body(EMPTY_RSS)
    assert title is None
    assert site_url is None
    assert lang_code is None
    assert has_entries is False

  def test_garbage_body_returns_all_none(self):
    title, site_url, lang_code, has_entries = probe_feed_body("not a feed at all")
    assert (title, site_url, lang_code, has_entries) == (None, None, None, False)


class TestExtractSiteUrl:
  def test_rss_link(self):
    assert extract_site_url(RSS_JA) == "https://example.jp/"

  def test_atom_link(self):
    assert extract_site_url(ATOM_JA) == "https://b.hatena.ne.jp/"

  def test_empty_returns_none(self):
    # Empty channel still has <link>, so this covers the "no link" variant.
    xml = """<?xml version="1.0"?><rss version="2.0"><channel><title>T</title></channel></rss>"""
    assert extract_site_url(xml) is None

  def test_garbage_returns_none(self):
    assert extract_site_url("garbage") is None


class TestExtractFeedCandidates:
  def test_rss_link_is_discovered(self):
    html = """<html><head>
      <link rel="alternate" type="application/rss+xml" href="/feed.xml" title="Main">
    </head></html>"""
    out = extract_feed_candidates(html, "https://example.com/blog")
    assert out == [{"href": "https://example.com/feed.xml", "title": "Main", "type": "application/rss+xml"}]

  def test_absolute_href_passes_through(self):
    html = """<link rel="alternate" type="application/atom+xml" href="https://cdn.example.com/atom">"""
    out = extract_feed_candidates(html, "https://example.com/")
    assert out[0]["href"] == "https://cdn.example.com/atom"

  def test_rss_preferred_over_atom_when_both_present(self):
    # _FEED_LINK_TYPES orders RSS before Atom; the output must reflect that.
    html = """<html><head>
      <link rel="alternate" type="application/atom+xml" href="/atom.xml">
      <link rel="alternate" type="application/rss+xml" href="/rss.xml">
    </head></html>"""
    out = extract_feed_candidates(html, "https://example.com/")
    assert [c["type"] for c in out] == ["application/rss+xml", "application/atom+xml"]

  def test_duplicates_removed(self):
    html = """<html><head>
      <link rel="alternate" type="application/rss+xml" href="/feed">
      <link rel="alternate" type="application/rss+xml" href="/feed">
    </head></html>"""
    assert len(extract_feed_candidates(html, "https://example.com/")) == 1

  def test_non_feed_links_ignored(self):
    html = """<html><head>
      <link rel="stylesheet" href="/s.css">
      <link rel="alternate" type="text/html" href="/en">
    </head></html>"""
    assert extract_feed_candidates(html, "https://example.com/") == []

  def test_no_links_returns_empty(self):
    assert extract_feed_candidates("<html><body>hi</body></html>", "https://example.com/") == []
