"""Tests for filter pattern parsing and matching.

Filter patterns support:
  - Plain substring (case-insensitive): "foo" matches title containing "foo".
  - Regex: "/pattern/" (wrapped in slashes). Invalid regex is silently dropped.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

import db
import repo
from repo import (
  DuplicateFilterError,
  _matches_regex_rules,
  _parse_filter_pattern,
  _split_filters,
  filter_key,
)
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


class TestFilterKey:
  """The key decides which rules count as duplicates of each other."""

  def test_case_is_ignored(self):
    assert filter_key("Apple", "title") == filter_key("apple", "title")

  def test_case_folding_matches_the_matcher(self):
    # Matching lowercases; casefold would merge these two even though only
    # one of them matches the title "strasse".
    assert filter_key("straße", "title") != filter_key("strasse", "title")

  def test_surrounding_space_is_significant(self):
    # Stored patterns match literally, so " spam " is its own rule. Input is
    # stripped by `add_filter` instead.
    assert filter_key(" spam ", "title") != filter_key("spam", "title")

  def test_regex_keeps_its_case(self):
    # Lowercasing a regex source would turn `\S` into `\s`.
    assert filter_key(r"/\S+/", "title") != filter_key(r"/\s+/", "title")

  def test_regex_is_distinct_from_the_same_keyword(self):
    assert filter_key("/abc/", "title") != filter_key("abc", "title")

  def test_body_and_both_are_the_same_scope(self):
    assert filter_key("spam", "body") == filter_key("spam", "both")

  def test_title_is_a_different_scope(self):
    assert filter_key("spam", "title") != filter_key("spam", "both")

  def test_url_is_its_own_scope(self):
    assert filter_key("spam", "url") != filter_key("spam", "title")
    assert filter_key("spam", "url") != filter_key("spam", "both")


@pytest.mark.usefixtures("clean_db")
class TestAddFilterDedupe:
  def test_equivalent_pattern_is_rejected(self):
    repo.add_filter("spam")
    with pytest.raises(DuplicateFilterError):
      repo.add_filter("  SPAM ")
    assert len(repo.list_filters()) == 1

  def test_pattern_is_stored_trimmed(self):
    added = repo.add_filter("  spam  ")
    assert added.pattern == "spam"

  def test_same_pattern_on_another_target_is_allowed(self):
    repo.add_filter("spam", target="title")
    repo.add_filter("spam", target="both")
    assert len(repo.list_filters()) == 2

  def test_regex_differing_only_in_case_is_allowed(self):
    repo.add_filter(r"/\S+/")
    repo.add_filter(r"/\s+/")
    assert len(repo.list_filters()) == 2

  def test_legacy_row_with_padding_does_not_block(self):
    # Rows written before patterns were stripped match literally, so the
    # stripped rule is a different rule and must still be addable.
    with db.connection() as conn:
      conn.execute("INSERT INTO filters (pattern, target) VALUES (' spam ', 'title')")
    repo.add_filter("spam")
    assert len(repo.list_filters()) == 2


def _article(
  *,
  url: str = "https://example.com/x",
  title: str = "",
  title_translated: str | None = None,
  content_snippet: str | None = None,
  summary: str | None = None,
) -> ArticleRow:
  return ArticleRow(
    url=url,
    title=title,
    title_translated=title_translated,
    source="test",
    published=datetime.now(tz=UTC),
    content_snippet=content_snippet,
    summary=summary,
  )


def _filter(pattern: str, target: str = "title") -> FilterRow:
  return FilterRow(id=1, pattern=pattern, target=target)


def _regex_hits(row: ArticleRow, *patterns: str, target: str = "title") -> bool:
  _, rules = _split_filters([_filter(p, target) for p in patterns])
  return _matches_regex_rules(row.model_dump(), rules)


class TestRegexRules:
  def test_regex_matches(self):
    assert _regex_hits(_article(title="Article 42"), r"/\d+/")

  def test_regex_case_insensitive(self):
    assert _regex_hits(_article(title="Hello"), "/hello/")

  def test_no_rules_never_match(self):
    assert not _regex_hits(_article(title="x"))

  def test_keyword_rules_are_left_to_sql(self):
    contains, rules = _split_filters([_filter("hello")])
    assert [f.pattern for f in contains] == ["hello"]
    assert rules == []

  def test_invalid_regex_silently_skipped(self):
    # Malformed regex should not raise; filter just doesn't match.
    assert not _regex_hits(_article(title="anything"), "/[unclosed/")

  def test_regex_length_limit_is_200(self):
    # Patterns > 200 chars are rejected as abuse guard.
    title = "a" * 300
    assert _regex_hits(_article(title=title), "/" + "a" * 200 + "/")
    assert not _regex_hits(_article(title=title), "/" + "a" * 201 + "/")

  def test_only_the_first_10000_chars_are_searched(self):
    assert _regex_hits(_article(title="a" * 9_999 + "X"), "/x/")
    assert not _regex_hits(_article(title="a" * 10_000 + "X"), "/x/")

  def test_empty_fields_are_not_searched(self):
    # `/^$/` would otherwise hide every article without a translated title.
    art = _article(title="Hello", title_translated=None, summary="")
    assert not _regex_hits(art, "/^$/")
    assert not _regex_hits(art, "/^$/", target="both")

  def test_target_title_checks_translated(self):
    art = _article(title="foo", title_translated="Translated 42")
    assert _regex_hits(art, r"/\d+/")

  def test_target_title_ignores_body_fields(self):
    art = _article(title="foo", content_snippet="spam 42", summary="spam 42")
    assert not _regex_hits(art, r"/\d+/")

  def test_target_both_checks_summary(self):
    art = _article(title="foo", summary="spam 42")
    assert _regex_hits(art, r"/\d+/", target="body")

  def test_regex_matches_url(self):
    art = _article(url="https://ads.example.com/x", title="foo")
    assert _regex_hits(art, r"/^https?://ads\./", target="url")

  def test_url_target_ignores_title(self):
    art = _article(url="https://example.com/x", title="https://ads.example.com")
    assert not _regex_hits(art, r"/^https?://ads\./", target="url")

  def test_multiple_rules_any_match(self):
    assert _regex_hits(_article(title="Hello World"), "/missing/", "/World/")


# --- Integration with list_articles (filters come from DB) ---


pytestmark_db = pytest.mark.usefixtures("clean_db")


def _feed() -> int:
  with db.connection() as conn:
    row = conn.execute(
      "INSERT INTO feeds (name, url) VALUES ('F', 'https://f.example.com/') RETURNING id"
    ).fetchone()
    assert row is not None
    return int(row["id"])


def _insert_article(
  feed_id: int,
  *,
  url: str,
  title: str,
  title_translated: str | None = None,
  content_snippet: str | None = None,
  summary: str | None = None,
) -> None:
  with db.connection() as conn:
    conn.execute(
      "INSERT INTO articles "
      "(url, feed_id, title, title_translated, source, content_snippet, summary) "
      "VALUES (%s, %s, %s, %s, 'F', %s, %s)",
      (url, feed_id, title, title_translated, content_snippet, summary),
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

  def test_filter_escapes_like_metacharacters(self):
    # `_` and `%` in the pattern must NOT be interpreted as wildcards.
    fid = _feed()
    _insert_article(fid, url="u1", title="50% off")
    _insert_article(fid, url="u2", title="500FF")  # would match if `%` were wild
    repo.add_filter("50%")
    rows = repo.list_articles()
    assert [r.url for r in rows] == ["u2"]

  def test_filter_substring_case_insensitive_unicode(self):
    # Runs a Japanese substring through the SQL keyword-filter path, guarding
    # against database encoding and collation regressions.
    fid = _feed()
    _insert_article(fid, url="u1", title="日本語のニュース")
    _insert_article(fid, url="u2", title="English news")
    repo.add_filter("日本語")
    rows = repo.list_articles()
    assert [r.url for r in rows] == ["u2"]

  def test_filter_target_body_matches_snippet(self):
    fid = _feed()
    _insert_article(fid, url="u1", title="ok", content_snippet="this contains AD spam")
    _insert_article(fid, url="u2", title="ok2", content_snippet="harmless")
    repo.add_filter("AD spam", target="body")
    rows = repo.list_articles()
    assert [r.url for r in rows] == ["u2"]

  def test_filter_target_title_ignores_body(self):
    fid = _feed()
    _insert_article(fid, url="u1", title="ok", content_snippet="AD spam in body")
    repo.add_filter("AD spam", target="title")
    rows = repo.list_articles()
    assert [r.url for r in rows] == ["u1"]

  def test_filter_target_url_matches_url(self):
    fid = _feed()
    _insert_article(fid, url="https://example.com/pr/1", title="ok")
    # The survivor carries the pattern in its title, so a URL rule that
    # leaked into other columns would drop it too.
    _insert_article(fid, url="https://example.com/news/2", title="see /pr/1 here")
    repo.add_filter("/PR/1", target="url")
    rows = repo.list_articles()
    assert [r.url for r in rows] == ["https://example.com/news/2"]

  def test_regex_filter_target_url_matches_url(self):
    fid = _feed()
    _insert_article(fid, url="https://ads.example.com/1", title="ok")
    _insert_article(fid, url="https://example.com/2", title="https://ads.example.com")
    repo.add_filter(r"/^https?://ads\./", target="url")
    rows = repo.list_articles()
    assert [r.url for r in rows] == ["https://example.com/2"]

  def test_regex_filter_still_works(self):
    fid = _feed()
    _insert_article(fid, url="u1", title="Article 42")
    _insert_article(fid, url="u2", title="Article foo")
    repo.add_filter(r"/\d+/")
    rows = repo.list_articles()
    assert [r.url for r in rows] == ["u2"]

  def test_invalid_regex_is_skipped(self):
    fid = _feed()
    _insert_article(fid, url="u1", title="anything")
    repo.add_filter("/[unclosed/")
    rows = repo.list_articles()
    assert [r.url for r in rows] == ["u1"]


@pytest.mark.usefixtures("clean_db")
class TestGetFeedStatsFilterIntegration:
  def test_stats_excludes_filtered_articles(self):
    fid = _feed()
    _insert_article(fid, url="u1", title="AD: sponsored")
    _insert_article(fid, url="u2", title="Real news")
    _insert_article(fid, url="u3", title="More real news")
    repo.add_filter("AD:")
    stats = repo.get_feed_stats()
    assert stats[fid].article_count == 2
    assert stats[fid].unread_count == 2

  def test_stats_no_filter(self):
    fid = _feed()
    _insert_article(fid, url="u1", title="x")
    _insert_article(fid, url="u2", title="y")
    stats = repo.get_feed_stats()
    assert stats[fid].article_count == 2

  def test_stats_with_regex_filter(self):
    fid = _feed()
    _insert_article(fid, url="u1", title="Article 42")
    _insert_article(fid, url="u2", title="Article foo")
    repo.add_filter(r"/\d+/")
    stats = repo.get_feed_stats()
    assert stats[fid].article_count == 1

  def test_stats_with_url_filters(self):
    fid = _feed()
    _insert_article(fid, url="https://example.com/pr/1", title="a")
    _insert_article(fid, url="https://ads.example.com/2", title="b")
    _insert_article(fid, url="https://example.com/news/3", title="c")
    repo.add_filter("/pr/1", target="url")  # contains
    repo.add_filter(r"/^https?://ads\./", target="url")  # regex
    stats = repo.get_feed_stats()
    assert stats[fid].article_count == 1

  def test_stats_combines_contains_and_regex(self):
    fid = _feed()
    _insert_article(fid, url="u1", title="AD: spam")
    _insert_article(fid, url="u2", title="Article 42")
    _insert_article(fid, url="u3", title="Real news")
    repo.add_filter("AD:")  # contains
    repo.add_filter(r"/\d+/")  # regex
    stats = repo.get_feed_stats()
    assert stats[fid].article_count == 1


def _visible(fid: int) -> set[str]:
  return {r.url for r in repo.list_articles(feed_id=fid)}


@pytest.mark.usefixtures("clean_db")
class TestContainsFilterSql:
  """Keyword rules run only in SQL; these pin which columns each one sees."""

  def test_case_insensitive_title_match(self):
    fid = _feed()
    _insert_article(fid, url="u1", title="Hello World")
    _insert_article(fid, url="u2", title="Goodbye")
    repo.add_filter("hELLO")
    assert _visible(fid) == {"u2"}

  def test_target_title_checks_translated(self):
    fid = _feed()
    _insert_article(fid, url="u1", title="foo", title_translated="Translated title")
    _insert_article(fid, url="u2", title="translated in the original")
    repo.add_filter("translated title")
    assert _visible(fid) == {"u2"}

  def test_target_title_ignores_summary(self):
    fid = _feed()
    _insert_article(fid, url="u1", title="foo", summary="AD spam")
    repo.add_filter("AD spam")
    assert _visible(fid) == {"u1"}

  def test_target_both_checks_summary(self):
    fid = _feed()
    _insert_article(fid, url="u1", title="foo", summary="contains AD-phrase")
    _insert_article(fid, url="u2", title="bar")
    repo.add_filter("AD-phrase", target="body")
    assert _visible(fid) == {"u2"}

  def test_url_target_ignores_title_and_body(self):
    fid = _feed()
    _insert_article(
      fid, url="u1", title="sponsored", title_translated="sponsored",
      content_snippet="sponsored", summary="sponsored",
    )
    repo.add_filter("sponsored", target="url")
    assert _visible(fid) == {"u1"}

  def test_other_targets_ignore_url(self):
    fid = _feed()
    _insert_article(fid, url="https://example.com/sponsored/1", title="foo")
    repo.add_filter("sponsored", target="title")
    repo.add_filter("sponsored", target="both")
    assert _visible(fid) == {"https://example.com/sponsored/1"}

  def test_rules_sharing_a_column_each_apply(self):
    # `title` and `both` rules land in the same per-column array.
    fid = _feed()
    _insert_article(fid, url="u1", title="alpha")
    _insert_article(fid, url="u2", title="beta")
    _insert_article(fid, url="u3", title="gamma", content_snippet="delta")
    _insert_article(fid, url="u4", title="kept")
    repo.add_filter("alpha")
    repo.add_filter("beta", target="both")
    repo.add_filter("delta", target="both")
    assert _visible(fid) == {"u4"}

  @pytest.mark.parametrize(
    ("pattern", "dropped", "kept"),
    [
      ("50%", "50% off", "500FF"),
      ("a_b", "a_b", "axb"),
      ("C:\\x", "C:\\x", "C:x"),
      ("!", "wow!", "wow"),
      ("\\%", "\\% sign", "% sign"),
    ],
  )
  def test_like_metacharacters_are_literal(self, pattern, dropped, kept):
    fid = _feed()
    _insert_article(fid, url="dropped", title=dropped)
    _insert_article(fid, url="kept", title=kept)
    repo.add_filter(pattern)
    assert _visible(fid) == {"kept"}

  @pytest.mark.parametrize(
    ("title", "pattern"),
    [
      ("ÄRGER", "ä"),
      ("ärger", "Ä"),
      ("İstanbul", "i̇"),
      ("istanbul", "İ"),
      ("ΣΟΦΟΣ", "σοφος"),
      ("Straße", "STRASSE"),
      ("ＡＢＣ", "ａｂｃ"),  # noqa: RUF001
    ],
  )
  def test_case_folding_matches_ilike(self, title, pattern):
    # The SQL used to be one `ILIKE` per rule; the rewrite must keep its
    # verdict even where Python's str.lower() would fold differently.
    fid = _feed()
    _insert_article(fid, url="u1", title=title)
    repo.add_filter(pattern)
    with db.connection() as conn:
      row = conn.execute(
        "SELECT %s ILIKE %s ESCAPE '!' AS hit",
        (title, "%" + pattern + "%"),
      ).fetchone()
    assert row is not None
    assert (_visible(fid) == set()) is row["hit"]

  def test_stats_and_list_agree(self):
    fid = _feed()
    _insert_article(fid, url="u1", title="AD: spam")
    _insert_article(fid, url="u2", title="Article 42")
    _insert_article(fid, url="u3", title="foo", summary="sponsored")
    _insert_article(fid, url="u4", title="Real news")
    _insert_article(fid, url="u5", title="Other news", title_translated="")
    repo.add_filter("ad:")
    repo.add_filter(r"/\d+/")
    repo.add_filter("SPONSORED", target="both")
    repo.add_filter("/^$/")
    assert _visible(fid) == {"u4", "u5"}
    assert repo.get_feed_stats()[fid].article_count == 2
