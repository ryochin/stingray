"""Table tests for app._is_html, which decides whether a probed URL is a web
page (scrape path) or a feed. A wrong answer sends a feed to the scraper or a
page to the feed parser, so the precedence between Content-Type and body
sniffing is pinned here."""

from __future__ import annotations

import pytest

from app import _is_html


@pytest.mark.parametrize(
  ("content_type", "body", "expected"),
  [
    # A known Content-Type wins over the body, in both directions.
    ("application/rss+xml", "<html><body></body></html>", False),
    ("application/atom+xml", "<!DOCTYPE html><html>", False),
    ("text/xml", "<html>", False),
    ("application/xml; charset=utf-8", "<html>", False),
    ("text/html", '<?xml version="1.0"?><rss></rss>', True),
    ("application/xhtml+xml", '<?xml version="1.0"?><feed></feed>', True),
    ("text/html; charset=utf-8", "", True),
    # Content-Type matching is case-insensitive.
    ("TEXT/HTML; Charset=UTF-8", "<rss></rss>", True),
    ("Application/RSS+XML", "<html>", False),
    # Unknown types fall through to body sniffing, which ignores leading space.
    ("text/plain", "  \n<!DOCTYPE html><html>", True),
    ("", "<!doctype HTML>", True),
    ("text/plain", "\t<HTML lang='en'>", True),
    # XML roots are decided before the "<html anywhere" fallback.
    ("text/plain", '<?xml version="1.0"?><html>', False),
    ("text/plain", "<rss><html>", False),
    ("text/plain", "<feed><html>", False),
    ("text/plain", "<rdf:RDF><html>", False),
    # Neither a doctype nor an XML root: "<html" anywhere in the prefix counts.
    ("text/plain", "<!-- lead --><html>", True),
    ("text/plain", "<!-- lead --><div>", False),
    # Only the first 500 characters after stripping leading whitespace count.
    ("text/plain", "x" * 500 + "<html>", False),
    ("text/plain", " " * 501 + "<html>", True),
    ("text/plain", "", False),
  ],
)
def test_is_html(content_type: str, body: str, expected: bool) -> None:
  assert _is_html(content_type, body) is expected
