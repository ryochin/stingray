"""Integration tests for tracker-stripping in `_parse_rss`.

These guard the two interface contracts:
  - `clean_url_fn` is honored when given, ignored when None.
  - The image-injection lookup (`item_images`) keys off the *raw* link;
    cleaning must not break thumbnail attachment.
"""

from __future__ import annotations

from feeds import _parse_rss
from url_cleaner import clean_url


def _feed_cfg() -> dict:
  return {"name": "Test", "url": "https://example.com/feed", "max_items": 50}


RSS_WITH_TRACKER = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>T</title>
    <link>https://example.com/</link>
    <item>
      <title>Post</title>
      <link>https://example.com/post?utm_source=feed&amp;id=1</link>
    </item>
  </channel>
</rss>
"""


RSS_WITH_TRACKER_AND_MEDIA = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
  <channel>
    <title>T</title>
    <link>https://example.com/</link>
    <item>
      <title>Post</title>
      <link>https://example.com/post?utm_source=feed&amp;id=1</link>
      <media:thumbnail url="https://cdn.example.com/img.jpg" />
    </item>
  </channel>
</rss>
"""


def test_clean_url_fn_strips_trackers_from_article_url():
  articles = _parse_rss(RSS_WITH_TRACKER, _feed_cfg(), clean_url_fn=clean_url)
  assert len(articles) == 1
  assert articles[0].url == "https://example.com/post?id=1"


def test_disabled_passes_through_raw_url():
  articles = _parse_rss(RSS_WITH_TRACKER, _feed_cfg(), clean_url_fn=None)
  assert len(articles) == 1
  # Verbatim: `&amp;` is decoded by feedparser to `&`.
  assert articles[0].url == "https://example.com/post?utm_source=feed&id=1"


def test_image_injection_survives_cleanup():
  # The raw <link> is what `_extract_item_images` keyed on; thumbnail
  # injection must still find the image after the article URL is cleaned.
  articles = _parse_rss(
    RSS_WITH_TRACKER_AND_MEDIA, _feed_cfg(), clean_url_fn=clean_url,
  )
  assert len(articles) == 1
  art = articles[0]
  assert art.url == "https://example.com/post?id=1"
  assert "https://cdn.example.com/img.jpg" in (art.content_html or "")
