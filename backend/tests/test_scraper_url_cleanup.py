"""Scraper parity test for tracker stripping.

`parse_web_page` is the HTML-scraping ingest path; its `Article.url`
must go through the same cleaner as the RSS path. Guards against the
two paths drifting.
"""

from __future__ import annotations

from scraper import parse_web_page
from url_cleaner import clean_url


HTML_WITH_TRACKER = """
<html><body>
  <div class="item">
    <a class="title" href="https://example.com/post?utm_source=site&id=1">Post one</a>
  </div>
  <div class="item">
    <a class="title" href="https://example.com/post2?fbclid=ABC&id=2">Post two</a>
  </div>
</body></html>
"""

RULES = {
  "item": ".item",
  "title": ".title",
  "link": ".title",
}


def _feed_cfg() -> dict:
  return {"name": "Web", "url": "https://example.com/", "max_items": 50}


def test_clean_url_fn_strips_trackers_from_scraped_url():
  articles = parse_web_page(
    HTML_WITH_TRACKER, RULES, _feed_cfg(), clean_url_fn=clean_url,
  )
  assert [a.url for a in articles] == [
    "https://example.com/post?id=1",
    "https://example.com/post2?id=2",
  ]


def test_disabled_passes_through_scraped_url():
  articles = parse_web_page(
    HTML_WITH_TRACKER, RULES, _feed_cfg(), clean_url_fn=None,
  )
  assert [a.url for a in articles] == [
    "https://example.com/post?utm_source=site&id=1",
    "https://example.com/post2?fbclid=ABC&id=2",
  ]
