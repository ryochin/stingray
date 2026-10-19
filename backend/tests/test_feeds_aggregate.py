"""Tests for the pure post-fetch steps of `feeds.fetch_all`.

`_filter_by_cutoff` runs per feed (age window + tz normalization) and
`_aggregate_feed_results` merges the per-feed outcomes (cross-feed URL dedup
plus a one-line summary log).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from feeds import _aggregate_feed_results, _filter_by_cutoff
from models import Article

CUTOFF = datetime(2026, 4, 10, 12, tzinfo=UTC)

FeedResult = tuple[list[Article], bool, str, Exception | None]


def _article(url: str, published: datetime | None = None, title: str = "T") -> Article:
  return Article(
    title=title,
    url=url,
    source="S",
    published=published,
    content_snippet="",
  )


def _feeds(n: int) -> list[dict[str, Any]]:
  return [{"name": f"F{i}"} for i in range(n)]


# --- _filter_by_cutoff ---


def test_cutoff_keeps_newer_and_boundary_drops_older() -> None:
  newer = _article("https://e.com/newer", CUTOFF + timedelta(seconds=1))
  boundary = _article("https://e.com/boundary", CUTOFF)
  older = _article("https://e.com/older", CUTOFF - timedelta(seconds=1))

  assert _filter_by_cutoff([newer, boundary, older], CUTOFF) == [newer, boundary]


def test_cutoff_keeps_undated_articles() -> None:
  undated = _article("https://e.com/undated")

  assert _filter_by_cutoff([undated], CUTOFF) == [undated]


def test_cutoff_treats_naive_as_utc_and_writes_it_back() -> None:
  # 12:00 naive == 12:00 UTC == cutoff, so it must be kept.
  naive = _article("https://e.com/naive", datetime(2026, 4, 10, 12))

  assert _filter_by_cutoff([naive], CUTOFF) == [naive]
  assert naive.published == datetime(2026, 4, 10, 12, tzinfo=UTC)
  assert naive.published is not None
  assert naive.published.tzinfo is UTC


def test_cutoff_compares_non_utc_offsets_correctly() -> None:
  jst = timezone(timedelta(hours=9))
  # 20:00 JST == 11:00 UTC (older); 22:00 JST == 13:00 UTC (newer).
  older = _article("https://e.com/older", datetime(2026, 4, 10, 20, tzinfo=jst))
  newer = _article("https://e.com/newer", datetime(2026, 4, 10, 22, tzinfo=jst))

  assert _filter_by_cutoff([older, newer], CUTOFF) == [newer]


def test_cutoff_preserves_order() -> None:
  items = [_article(f"https://e.com/{i}", CUTOFF + timedelta(hours=i)) for i in (3, 1, 2)]

  assert _filter_by_cutoff(items, CUTOFF) == items


# --- _aggregate_feed_results ---


def test_aggregate_concatenates_in_feed_order() -> None:
  a = _article("https://e.com/a")
  b = _article("https://e.com/b")
  c = _article("https://e.com/c")
  results: list[FeedResult] = [([a, b], False, "rss", None), ([c], False, "rss", None)]

  assert _aggregate_feed_results(_feeds(2), results) == [a, b, c]


def test_aggregate_dedups_urls_across_feeds_first_wins() -> None:
  first = _article("https://e.com/dup", title="first")
  second = _article("https://e.com/dup", title="second")
  results: list[FeedResult] = [([first], False, "rss", None), ([second], False, "rss", None)]

  merged = _aggregate_feed_results(_feeds(2), results)

  assert len(merged) == 1
  assert merged[0].title == "first"


def test_aggregate_keeps_other_feeds_when_one_fails_or_is_empty() -> None:
  a = _article("https://e.com/a")
  results: list[FeedResult] = [
    ([], False, "rss", RuntimeError("boom")),
    ([], True, "rss", None),
    ([a], False, "web", None),
  ]

  assert _aggregate_feed_results(_feeds(3), results) == [a]


def test_aggregate_rejects_length_mismatch() -> None:
  results: list[FeedResult] = [([], False, "rss", None)]

  with pytest.raises(ValueError):
    _aggregate_feed_results(_feeds(2), results)


def test_aggregate_summary_classifies_feeds(capsys: pytest.CaptureFixture[str]) -> None:
  a = _article("https://e.com/a")
  dup = _article("https://e.com/a")
  b = _article("https://e.com/b")
  results: list[FeedResult] = [
    ([a], False, "rss", None),  # fresh
    ([dup, b], True, "rss", None),  # cached
    ([], False, "web-norules", RuntimeError("boom")),  # error wins over kind
    ([], True, "web-norules", None),  # web-norules wins over cached
  ]

  _aggregate_feed_results(_feeds(4), results)

  out = capsys.readouterr().out
  assert "Feeds: 1 fresh, 1 cached, 1 errors, 1 skipped (no rules)" in out
  # within window counts after dedup, total before.
  assert "items: 2 within window, 3 total." in out


def test_aggregate_summary_omits_zero_errors_and_skips(capsys: pytest.CaptureFixture[str]) -> None:
  results: list[FeedResult] = [([_article("https://e.com/a")], False, "rss", None)]

  _aggregate_feed_results(_feeds(1), results)

  out = capsys.readouterr().out
  assert "Feeds: 1 fresh, 0 cached | items: 1 within window, 1 total." in out
