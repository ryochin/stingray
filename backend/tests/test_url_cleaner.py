"""Tests for `url_cleaner.clean_url`.

The cleaner is a pure function — these tests pin both the removal
behavior and the accepted canonicalizations (e.g. `%20` → `+`,
`?flag` → `?flag=`) so future regressions surface here, not via
duplicated rows in the DB.
"""

from __future__ import annotations

from url_cleaner import clean_url


def test_no_op_when_no_query():
  assert clean_url("https://example.com/foo") == "https://example.com/foo"


def test_utm_prefix_removed():
  assert (
    clean_url("https://example.com/x?utm_source=feed&utm_medium=rss&p=1")
    == "https://example.com/x?p=1"
  )


def test_exact_match_click_id_removed():
  assert (
    clean_url("https://example.com/post?fbclid=ABC&id=42")
    == "https://example.com/post?id=42"
  )


def test_qiita_sample():
  # The motivating example from the task description.
  src = (
    "https://qiita.com/nhatcaofedev/items/1b5b5016b5a74fe27fe5"
    "?utm_campaign=popular_items&utm_medium=feed&utm_source=popular_items"
  )
  assert (
    clean_url(src)
    == "https://qiita.com/nhatcaofedev/items/1b5b5016b5a74fe27fe5"
  )


def test_case_insensitive_param_name():
  assert (
    clean_url("https://example.com/?UTM_Source=x&FBCLID=y&Keep=1")
    == "https://example.com/?Keep=1"
  )


def test_non_tracker_preserved():
  assert (
    clean_url("https://example.com/search?q=hello&page=2")
    == "https://example.com/search?q=hello&page=2"
  )


def test_fragment_preserved():
  assert (
    clean_url("https://example.com/?utm_source=x#section")
    == "https://example.com/#section"
  )


def test_empty_query_after_stripping_drops_question_mark():
  assert (
    clean_url("https://example.com/?utm_source=x&utm_medium=y")
    == "https://example.com/"
  )


def test_flag_without_equals_becomes_flag_equals():
  # `parse_qsl(..., keep_blank_values=True)` accepts `?flag` and
  # `urlencode` emits `?flag=`. We accept the canonicalization.
  assert clean_url("https://example.com/?flag") == "https://example.com/?flag="


def test_percent_encoded_space_becomes_plus():
  # Same canonicalization story for kept values.
  assert (
    clean_url("https://example.com/?q=a%20b&utm_source=x")
    == "https://example.com/?q=a+b"
  )


def test_hostname_and_path_untouched():
  src = (
    "https://Sub.Example.com:8080/path/to/article.html"
    "?utm_source=x&id=42&fbclid=YYY#frag"
  )
  expected = (
    "https://Sub.Example.com:8080/path/to/article.html?id=42#frag"
  )
  assert clean_url(src) == expected


def test_idempotent():
  src = "https://example.com/x?utm_source=a&fbclid=b&id=42"
  once = clean_url(src)
  assert clean_url(once) == once


def test_hubspot_full_set_removed():
  # `_hs` as a prefix would miss `__hs*` — these must all go via exact.
  src = (
    "https://example.com/post"
    "?__hssc=1&__hstc=2&__hsfp=3&_hsenc=4&_hsmi=5&id=1"
  )
  assert clean_url(src) == "https://example.com/post?id=1"


def test_ck_subscriber_id_exact_other_ck_preserved():
  # Guards against the previously considered (too broad) `ck_` prefix.
  src = "https://example.com/?ck_subscriber_id=A&ck_color=blue"
  assert clean_url(src) == "https://example.com/?ck_color=blue"
