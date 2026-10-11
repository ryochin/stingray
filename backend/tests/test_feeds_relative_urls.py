"""Integration tests for relative-URL resolution inside `_parse_rss`.

The contract under test is which base the resolution uses: the item's own
`<link>`, never the feed URL. Generated mirror feeds host the XML on a
different origin than the articles they point at, so getting this wrong
silently rewrites every link onto the wrong site.
"""

from __future__ import annotations

import feeds
from feeds import _parse_rss
from url_cleaner import clean_url

# The feed lives on a different host than the articles — as with a machi.to
# thread republished through a feed generator.
RSS_MIRROR = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>T</title>
    <link>https://source.example/</link>
    <item>
      <title>51</title>
      <link>https://source.example/thread/1/51?utm_source=feed</link>
      <content:encoded><![CDATA[<a href="/link.cgi?URL=https://out.example/p">o</a>]]></content:encoded>
    </item>
  </channel>
</rss>
"""


def _feed_cfg() -> dict:
  return {"name": "Test", "url": "https://mirror.example/feed.xml", "max_items": 50}


RSS_MEDIA = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>T</title>
    <link>https://source.example/</link>
    <item>
      <title>Media</title>
      <link>https://source.example/thread/1/52</link>
      <content:encoded><![CDATA[<video src="/v.mp4" poster="/p.jpg"></video><audio src="/a.mp3"></audio>]]></content:encoded>
    </item>
  </channel>
</rss>
"""


RSS_LEGACY_ATTRS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>T</title>
    <link>https://source.example/</link>
    <item>
      <title>Legacy</title>
      <link>https://source.example/thread/1/53</link>
      <content:encoded><![CDATA[<map name="m"><area href="/d" shape="rect" coords="0,0,1,1"></map><table background="/t.png"><tr><th background="/h.png">h</th><td background="/c.png">c</td></tr></table><blockquote cite="/b">b</blockquote><q cite="/q">q</q><ins cite="/i">i</ins><del cite="/x">x</del>]]></content:encoded>
    </item>
  </channel>
</rss>
"""

RSS_THUMBNAIL = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
  <channel>
    <title>T</title>
    <link>https://source.example/</link>
    <item>
      <title>Thumb</title>
      <link>https://source.example/thread/1/54</link>
      <description>body</description>
      <media:thumbnail url="../thumb.jpg"/>
    </item>
  </channel>
</rss>
"""


def test_relative_link_resolves_against_item_link_not_feed_url():
  articles = _parse_rss(RSS_MIRROR, _feed_cfg(), clean_url_fn=clean_url)
  assert len(articles) == 1
  html = articles[0].content_html or ""
  assert 'href="https://source.example/link.cgi?URL=https://out.example/p"' in html
  assert "mirror.example" not in html


def test_media_urls_survive_feedparser_and_get_resolved():
  # Guards the attribute list in html_urls against feedparser's sanitizer:
  # these tags pass through, so their relative URLs must be resolved too.
  articles = _parse_rss(RSS_MEDIA, _feed_cfg(), clean_url_fn=clean_url)
  html = articles[0].content_html or ""
  for expected in (
    'src="https://source.example/v.mp4"',
    'poster="https://source.example/p.jpg"',
    'src="https://source.example/a.mp3"',
  ):
    assert expected in html


def test_legacy_url_attrs_survive_feedparser_and_get_resolved():
  # Image maps, table backgrounds and `cite` also pass both sanitizers; left
  # relative they would point at our own origin.
  articles = _parse_rss(RSS_LEGACY_ATTRS, _feed_cfg(), clean_url_fn=clean_url)
  html = articles[0].content_html or ""
  for expected in (
    'href="https://source.example/d"',
    'background="https://source.example/t.png"',
    'background="https://source.example/h.png"',
    'background="https://source.example/c.png"',
    'cite="https://source.example/b"',
    'cite="https://source.example/q"',
    'cite="https://source.example/i"',
    'cite="https://source.example/x"',
  ):
    assert expected in html


def test_injected_thumbnail_is_resolved_too():
  # The thumbnail is injected into the body before resolution runs; reversing
  # that order would leave only the injected image relative.
  articles = _parse_rss(RSS_THUMBNAIL, _feed_cfg(), clean_url_fn=clean_url)
  html = articles[0].content_html or ""
  assert 'src="https://source.example/thread/thumb.jpg"' in html


def test_resolution_base_is_the_uncleaned_item_link(monkeypatch):
  # `url_cleaner` only drops query params, so raw and cleaned links resolve
  # identically — spy on the argument instead of asserting on the output.
  seen: list[str] = []
  original = feeds.absolutize_html_urls

  def spy(html_text: str, base_url: str) -> str:
    seen.append(base_url)
    return original(html_text, base_url)

  monkeypatch.setattr(feeds, "absolutize_html_urls", spy)
  articles = _parse_rss(RSS_MIRROR, _feed_cfg(), clean_url_fn=clean_url)

  assert articles[0].url == "https://source.example/thread/1/51"
  assert seen == ["https://source.example/thread/1/51?utm_source=feed"]
