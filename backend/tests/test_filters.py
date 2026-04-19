"""Tests for filter pattern parsing and matching.

Filter patterns support:
  - Plain substring (case-insensitive): "foo" matches title containing "foo".
  - Regex: "/pattern/" (wrapped in slashes). Invalid regex is silently dropped.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import db
import repo
from repo import _article_matches_filter, _parse_filter_pattern
from schemas import ArticleRow, FilterRow


class TestParsePattern:
  def test_plain_string(self):
    assert _parse_filter_pattern("hello") == (False, "hello")

  def test_slash_wrapped_is_regex(self):
    assert _parse_filter_pattern("/ab.c/") == (True, "ab.c")

  def test_single_slash_not_regex(self):
    assert _parse_filter_pattern("/") == (False, "/")

  def test_two_slashes_not_regex(self):
    # Need length > 2 for a valid regex wrapper.
    assert _parse_filter_pattern("//") == (False, "//")

  def test_opening_slash_only_not_regex(self):
    assert _parse_filter_pattern("/foo") == (False, "/foo")


def _article(
  *,
  title: str = "",
  title_translated: str | None = None,
  content_snippet: str | None = None,
  summary: str | None = None,
) -> ArticleRow:
  return ArticleRow(
    url="https://example.com/x",
    title=title,
    title_translated=title_translated,
    source="test",
    published=datetime.now(tz=timezone.utc),
    content_snippet=content_snippet,
    summary=summary,
  )


def _filter(pattern: str, target: str = "title") -> FilterRow:
  return FilterRow(id=1, pattern=pattern, target=target)


class TestMatchesFilterSubstring:
  def test_case_insensitive_title_match(self):
    assert _article_matches_filter(_article(title="Hello World"), [_filter("hello")])

  def test_no_match(self):
    assert not _article_matches_filter(_article(title="Hello"), [_filter("goodbye")])

  def test_empty_filter_list_never_matches(self):
    assert not _article_matches_filter(_article(title="x"), [])

  def test_target_title_checks_translated(self):
    art = _article(title="foo", title_translated="日本語タイトル")
    assert _article_matches_filter(art, [_filter("日本語")])

  def test_target_title_ignores_body_fields(self):
    # target="title" must NOT peek into content_snippet/summary.
    art = _article(title="foo", content_snippet="AD spam here", summary="AD summary")
    assert not _article_matches_filter(art, [_filter("AD spam")])

  def test_target_body_includes_all_fields(self):
    art = _article(title="foo", content_snippet="AD spam here")
    assert _article_matches_filter(art, [_filter("AD spam", target="body")])

  def test_target_body_checks_summary(self):
    art = _article(title="foo", summary="contains AD-phrase")
    assert _article_matches_filter(art, [_filter("AD-phrase", target="body")])


class TestMatchesFilterRegex:
  def test_regex_matches(self):
    assert _article_matches_filter(_article(title="Article 42"), [_filter(r"/\d+/")])

  def test_regex_case_insensitive(self):
    assert _article_matches_filter(_article(title="Hello"), [_filter("/hello/")])

  def test_invalid_regex_silently_skipped(self):
    # Malformed regex should not raise; filter just doesn't match.
    assert not _article_matches_filter(_article(title="anything"), [_filter("/[unclosed/")])

  def test_overlong_regex_skipped(self):
    # Patterns > 200 chars are rejected as abuse guard.
    long_pat = "/" + "a" * 201 + "/"
    assert not _article_matches_filter(_article(title="a" * 300), [_filter(long_pat)])

  def test_multiple_filters_any_match(self):
    art = _article(title="Hello World")
    filters = [_filter("missing"), _filter("World")]
    assert _article_matches_filter(art, filters)


# --- Integration with list_articles (filters come from DB) ---


pytestmark_db = pytest.mark.usefixtures("clean_db")


def _feed() -> int:
  with db.connection() as conn:
    row = conn.execute(
      "INSERT INTO feeds (name, url) VALUES ('F', 'https://f.example.com/') RETURNING id"
    ).fetchone()
    assert row is not None
    return int(row["id"])


def _insert_article(feed_id: int, *, url: str, title: str) -> None:
  with db.connection() as conn:
    conn.execute(
      "INSERT INTO articles (url, feed_id, title, source) VALUES (%s, %s, %s, 'F')",
      (url, feed_id, title),
    )


@pytest.mark.usefixtures("clean_db")
class TestListArticlesFilterIntegration:
  def test_filter_drops_matching_articles(self):
    fid = _feed()
    _insert_article(fid, url="u1", title="AD: sponsored content")
    _insert_article(fid, url="u2", title="Real news")
    repo.add_filter("AD:")
    rows = repo.list_articles()
    assert [r.url for r in rows] == ["u2"]

  def test_no_filters_returns_all(self):
    fid = _feed()
    _insert_article(fid, url="u1", title="AD: sponsored")
    _insert_article(fid, url="u2", title="Real news")
    rows = repo.list_articles()
    assert {r.url for r in rows} == {"u1", "u2"}
