"""Field reading in _parse_rss: body priority, link filtering, and dates."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from feeds import _parse_rss

FEED_CFG: dict[str, Any] = {"name": "Feed", "url": "https://feed.example/rss"}


def _rss(item_xml: str) -> str:
  return f"""<?xml version="1.0"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel><title>F</title><link>https://feed.example/</link>
<item>{item_xml}</item>
</channel></rss>"""


def _atom(entry_xml: str) -> str:
  return f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>F</title>
<entry>{entry_xml}</entry>
</feed>"""


LINK = "<link>https://site.example/a</link>"


def test_content_encoded_is_preferred_over_description() -> None:
  [article] = _parse_rss(_rss(
    f"<title>T</title>{LINK}<description>short</description>"
    "<content:encoded><![CDATA[<p>full body</p>]]></content:encoded>"
  ), FEED_CFG)
  assert article.content_html == "<p>full body</p>"
  assert article.content_snippet == "full body"


def test_description_is_used_without_content() -> None:
  [article] = _parse_rss(_rss(f"<title>T</title>{LINK}<description>&lt;b&gt;desc&lt;/b&gt;</description>"), FEED_CFG)
  assert article.content_html == "<b>desc</b>"
  assert article.content_snippet == "desc"


def test_empty_content_encoded_falls_back_to_description() -> None:
  [article] = _parse_rss(_rss(
    f"<title>T</title>{LINK}<description>desc</description><content:encoded></content:encoded>"
  ), FEED_CFG)
  assert article.content_snippet == "desc"


def test_atom_summary_is_used_without_content() -> None:
  [article] = _parse_rss(_atom(
    '<title>T</title><link href="https://site.example/a"/><summary>sum</summary>'
  ), FEED_CFG)
  assert article.content_snippet == "sum"


def test_atom_content_is_preferred_over_summary() -> None:
  [article] = _parse_rss(_atom(
    '<title>T</title><link href="https://site.example/a"/>'
    '<summary>sum</summary><content type="html">&lt;p&gt;body&lt;/p&gt;</content>'
  ), FEED_CFG)
  assert article.content_html == "<p>body</p>"


def test_no_body_yields_empty_fields() -> None:
  [article] = _parse_rss(_rss(f"<title>T</title>{LINK}"), FEED_CFG)
  assert (article.content_html, article.content_snippet) == ("", "")


def test_entry_without_link_is_skipped() -> None:
  assert _parse_rss(_rss("<title>T</title>"), FEED_CFG) == []


def test_entry_with_non_http_link_is_skipped() -> None:
  assert _parse_rss(_rss("<title>T</title><link>ftp://site.example/a</link>"), FEED_CFG) == []


def test_title_and_url_are_read() -> None:
  [article] = _parse_rss(_rss(f"<title>Hello</title>{LINK}"), FEED_CFG)
  assert (article.title, article.url, article.source) == ("Hello", "https://site.example/a", "Feed")


def test_published_date_is_read() -> None:
  [article] = _parse_rss(_rss(f"<title>T</title>{LINK}<pubDate>Tue, 03 Feb 2026 04:05:06 GMT</pubDate>"), FEED_CFG)
  assert article.published == datetime(2026, 2, 3, 4, 5, 6, tzinfo=UTC)


def test_atom_updated_is_used_without_published() -> None:
  [article] = _parse_rss(_atom(
    '<title>T</title><link href="https://site.example/a"/><updated>2026-02-03T04:05:06Z</updated>'
  ), FEED_CFG)
  assert article.published == datetime(2026, 2, 3, 4, 5, 6, tzinfo=UTC)


def test_atom_published_is_preferred_over_updated() -> None:
  [article] = _parse_rss(_atom(
    '<title>T</title><link href="https://site.example/a"/>'
    "<published>2026-01-01T00:00:00Z</published><updated>2026-02-03T04:05:06Z</updated>"
  ), FEED_CFG)
  assert article.published == datetime(2026, 1, 1, tzinfo=UTC)


def test_missing_date_is_none() -> None:
  [article] = _parse_rss(_rss(f"<title>T</title>{LINK}"), FEED_CFG)
  assert article.published is None


def test_unparseable_date_is_none() -> None:
  [article] = _parse_rss(_rss(f"<title>T</title>{LINK}<pubDate>not a date</pubDate>"), FEED_CFG)
  assert article.published is None


def test_max_items_limits_entries() -> None:
  items = "".join(f"<item><title>T{i}</title><link>https://site.example/{i}</link></item>" for i in range(5))
  body = f'<?xml version="1.0"?><rss version="2.0"><channel><title>F</title>{items}</channel></rss>'
  articles = _parse_rss(body, {**FEED_CFG, "max_items": 2})
  assert [a.title for a in articles] == ["T0", "T1"]
