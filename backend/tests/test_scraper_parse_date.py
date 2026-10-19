"""Tests for `scraper._parse_date`, the date parser for web-page feeds.

Every successful parse must be tz-aware: the manual single-feed fetch path
upserts the result without `_filter_by_cutoff`, so a naive value would be
read in the DB session's TimeZone instead of UTC.
"""

from __future__ import annotations

import locale
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone

import pytest

from scraper import _parse_date

JST = timezone(timedelta(hours=9))


@pytest.fixture(autouse=True)
def c_time_locale() -> Iterator[None]:
  # English month names (%B / %b) only parse under an English-ish LC_TIME.
  saved = locale.setlocale(locale.LC_TIME)
  locale.setlocale(locale.LC_TIME, "C")
  try:
    yield
  finally:
    locale.setlocale(locale.LC_TIME, saved)


@pytest.mark.parametrize("text", [None, "", "   "])
def test_empty_input_returns_none(text: str | None) -> None:
  assert _parse_date(text) is None


@pytest.mark.parametrize(
  ("text", "expected"),
  [
    ("2026-04-10T10:00:00+09:00", datetime(2026, 4, 10, 10, tzinfo=JST)),
    ("2026-04-10T10:00:00Z", datetime(2026, 4, 10, 10, tzinfo=UTC)),
    # Naive ISO strings are pinned to UTC, like the other formats.
    ("2026-04-10T10:00:00", datetime(2026, 4, 10, 10, tzinfo=UTC)),
    ("2026-04-10", datetime(2026, 4, 10, tzinfo=UTC)),
  ],
)
def test_iso_8601(text: str, expected: datetime) -> None:
  result = _parse_date(text)
  assert result == expected
  assert result is not None
  assert result.utcoffset() == expected.utcoffset()


@pytest.mark.parametrize(
  "text",
  [
    # Not zero-padded, so fromisoformat rejects it and %Y-%m-%d picks it up.
    "2026-4-10",
    "2026/04/10",
    "April 10, 2026",
    "10 Apr 2026",
    "Apr 10, 2026",
    "2026年4月10日",
  ],
)
def test_fallback_formats_are_utc(text: str) -> None:
  result = _parse_date(text)
  assert result == datetime(2026, 4, 10, tzinfo=UTC)
  assert result is not None
  assert result.tzinfo is UTC


def test_strips_surrounding_whitespace() -> None:
  assert _parse_date("  2026-04-10T10:00:00+09:00\n") == datetime(2026, 4, 10, 10, tzinfo=JST)


def test_japanese_date_found_inside_text() -> None:
  # parse_web_page falls back to the first 200 chars of the item snippet.
  assert _parse_date("更新: 2026年4月10日 のお知らせ") == datetime(2026, 4, 10, tzinfo=UTC)


def test_invalid_japanese_date_returns_none() -> None:
  assert _parse_date("2026年2月30日") is None


def test_english_format_must_match_whole_string() -> None:
  # Only the Japanese pattern is searched; strptime formats need a full match.
  assert _parse_date("Posted on Apr 10, 2026") is None


def test_unparseable_returns_none() -> None:
  assert _parse_date("yesterday") is None
