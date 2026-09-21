"""Unit tests for relative-URL resolution in article HTML."""

from __future__ import annotations

from html_urls import absolutize_html_urls

BASE = "https://machi.to/bbs/read.cgi/tokyo/1789130379/51"


def test_root_relative_href_is_resolved():
  html = '<a href="/bbs/link.cgi?URL=https://example.com/x">x</a>'
  out = absolutize_html_urls(html, BASE)
  assert 'href="https://machi.to/bbs/link.cgi?URL=https://example.com/x"' in out


def test_document_relative_href_is_resolved():
  out = absolutize_html_urls('<a href="../other.html">o</a>', BASE)
  assert 'href="https://machi.to/bbs/read.cgi/tokyo/other.html"' in out


def test_protocol_relative_url_takes_base_scheme():
  out = absolutize_html_urls('<img src="//cdn.example.com/i.png" />', BASE)
  assert 'src="https://cdn.example.com/i.png"' in out


def test_source_src_is_resolved():
  out = absolutize_html_urls('<source src="/media/a.webm" />', BASE)
  assert 'src="https://machi.to/media/a.webm"' in out


def test_media_and_longdesc_attributes_are_resolved():
  # These survive feedparser's sanitizer, so they reach us relative.
  html = (
    '<video src="/v.mp4" poster="/p.jpg"></video>'
    '<audio src="/a.mp3"></audio>'
    '<img src="/i.png" longdesc="/d.html" />'
  )
  out = absolutize_html_urls(html, BASE)
  for expected in (
    'src="https://machi.to/v.mp4"',
    'poster="https://machi.to/p.jpg"',
    'src="https://machi.to/a.mp3"',
    'longdesc="https://machi.to/d.html"',
  ):
    assert expected in out


def test_absolute_urls_are_left_alone():
  html = '<a href="https://example.com/x">x</a>'
  assert absolutize_html_urls(html, BASE) is html


def test_special_schemes_and_fragments_are_skipped():
  # A bare fragment points inside the rendered article body, so rewriting it
  # onto the source site would break the in-page jump.
  html = (
    '<a href="#fn1">fn</a>'
    '<a href="mailto:a@example.com">m</a>'
    '<a href="javascript:void(0)">j</a>'
    '<a href="tel:+81312345678">t</a>'
    '<img src="data:image/gif;base64,R0lGOD" />'
  )
  assert absolutize_html_urls(html, BASE) is html


def test_empty_attribute_is_skipped():
  # `urljoin(base, "")` yields the article URL — a link to itself.
  html = '<a href="">empty</a>'
  assert absolutize_html_urls(html, BASE) is html


def test_uppercase_scheme_is_left_alone():
  # urljoin would lowercase the scheme, forcing a pointless re-serialization.
  html = '<a href="HTTPS://EXAMPLE.COM/x">x</a>'
  assert absolutize_html_urls(html, BASE) is html


def test_malformed_url_does_not_raise():
  # `//[bad` is protocol-relative, so it reaches urljoin (which raises
  # "Invalid IPv6 URL") instead of being skipped as an absolute URL.
  html = '<a href="//[bad">x</a>'
  assert absolutize_html_urls(html, BASE) is html


def test_malformed_base_does_not_raise():
  assert absolutize_html_urls('<a href="/x">x</a>', "//[bad") == '<a href="/x">x</a>'


def test_empty_input_or_base_passes_through():
  assert absolutize_html_urls("", BASE) == ""
  assert absolutize_html_urls('<a href="/x">x</a>', "") == '<a href="/x">x</a>'


def test_unchanged_markup_is_returned_verbatim():
  # A BeautifulSoup round-trip would normalize this to `<p>A</p><p>B</p>`;
  # with nothing to resolve, the fragment must not be re-serialized at all.
  html = "<p>A<p>B"
  assert absolutize_html_urls(html, BASE) is html


def test_malformed_nesting_is_normalized_when_a_url_changes():
  # Documents a known limitation: `html.parser` is not an HTML5 tree builder,
  # so re-serializing crossed tags can move content. Only fragments that are
  # already malformed *and* carry a relative URL are affected.
  out = absolutize_html_urls('<p><div>A</p>B</div><img src="/i" />', BASE)
  assert 'src="https://machi.to/i"' in out
  assert out == '<p><div>A</div></p>B<img src="https://machi.to/i"/>'


def test_is_idempotent():
  html = '<a href="/x">x</a>'
  once = absolutize_html_urls(html, BASE)
  assert absolutize_html_urls(once, BASE) is once
