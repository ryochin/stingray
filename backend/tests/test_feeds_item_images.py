"""Table tests for feeds._extract_item_images, which recovers thumbnails
feedparser ignores. RSS and Atom are separate code paths with their own link
selection and image priority, so each is pinned independently."""

from __future__ import annotations

import pytest

import feeds

MEDIA_NS: str = 'xmlns:media="http://search.yahoo.com/mrss/"'
LINK: str = "https://example.com/a"


def _rss(*items: str) -> str:
  return (
    f'<?xml version="1.0"?><rss version="2.0" {MEDIA_NS}><channel><title>T</title>'
    + "".join(f"<item><title>t</title>{item}</item>" for item in items)
    + "</channel></rss>"
  )


def _atom(*entries: str) -> str:
  return (
    f'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" {MEDIA_NS}><title>T</title>'
    + "".join(f"<entry><title>t</title>{entry}</entry>" for entry in entries)
    + "</feed>"
  )


IMAGE: str = "<image>https://img.test/image.jpg</image>"
CONTENT: str = '<media:content url="https://img.test/content.jpg"/>'
THUMB: str = '<media:thumbnail url="https://img.test/thumb.jpg"/>'
ENC_JPEG: str = '<enclosure type="image/jpeg" url="https://img.test/enc.jpg"/>'
ENC_AUDIO: str = '<enclosure type="audio/mpeg" url="https://img.test/enc.mp3"/>'


@pytest.mark.parametrize(
  ("elements", "expected"),
  [
    # Priority is image > media:content > media:thumbnail > enclosure. Each
    # adjacent pair lists the lower-priority element first in document order.
    ([CONTENT, IMAGE], "https://img.test/image.jpg"),
    ([THUMB, CONTENT], "https://img.test/content.jpg"),
    ([ENC_JPEG, THUMB], "https://img.test/thumb.jpg"),
    ([ENC_JPEG], "https://img.test/enc.jpg"),
    ([ENC_AUDIO], None),
    ([], None),
  ],
  ids=["image>content", "content>thumbnail", "thumbnail>enclosure", "enclosure", "audio", "none"],
)
def test_rss_image_priority(elements: list[str], expected: str | None) -> None:
  body = _rss(f"<link>{LINK}</link>" + "".join(elements))
  assert feeds._extract_item_images(body) == ({LINK: expected} if expected else {})


def test_rss_items_without_a_link_are_skipped() -> None:
  body = _rss(THUMB, f"<link>  </link>{THUMB}", f"<link>{LINK}</link>{THUMB}")
  assert feeds._extract_item_images(body) == {LINK: "https://img.test/thumb.jpg"}


@pytest.mark.parametrize(
  ("links", "expected_key"),
  [
    # rel="alternate" wins even when another link comes first.
    (
      '<link rel="self" href="https://example.com/self"/>'
      '<link rel="alternate" href="https://example.com/alt"/>',
      "https://example.com/alt",
    ),
    # Without an alternate, the first link is used whatever its rel.
    (
      '<link rel="self" href="https://example.com/self"/><link href="https://example.com/plain"/>',
      "https://example.com/self",
    ),
    # An empty alternate href drops the entry rather than falling back.
    ('<link rel="alternate" href=""/><link href="https://example.com/plain"/>', None),
  ],
  ids=["alternate-first", "first-link", "empty-alternate"],
)
def test_atom_link_selection(links: str, expected_key: str | None) -> None:
  body = _atom(links + THUMB)
  expected = {expected_key: "https://img.test/thumb.jpg"} if expected_key else {}
  assert feeds._extract_item_images(body) == expected


def test_atom_media_content_beats_thumbnail() -> None:
  body = _atom(f'<link href="{LINK}"/>{THUMB}{CONTENT}')
  assert feeds._extract_item_images(body) == {LINK: "https://img.test/content.jpg"}


def test_broken_xml_yields_no_images() -> None:
  assert feeds._extract_item_images("<rss><channel><item>") == {}
