"""Tests for backend/lang.py — pure language-detection heuristics."""

from __future__ import annotations

import pytest

import lang


class TestDetectByScript:
  def test_hiragana(self):
    assert lang.detect_lang_by_script("これはテスト") == "ja"

  def test_katakana(self):
    assert lang.detect_lang_by_script("テスト") == "ja"

  def test_kanji_only_not_detected(self):
    # Kanji alone is ambiguous (shared with zh); profile only registers kana.
    assert lang.detect_lang_by_script("朝日新聞") is None

  def test_english_not_detected(self):
    assert lang.detect_lang_by_script("Hello world") is None

  def test_empty(self):
    assert lang.detect_lang_by_script("") is None

  def test_mixed_with_kana_detected(self):
    # Kana alongside latin → ja. (Kanji-only "速報" would NOT match — only kana in profile.)
    assert lang.detect_lang_by_script("Breaking: ニュース") == "ja"


class TestDetectByTld:
  @pytest.mark.parametrize("url", [
    "https://example.jp/rss",
    "https://www.asahi.com.jp/",  # .jp suffix wins even with .com in middle
    "http://sub.domain.co.jp/feed",
  ])
  def test_jp_tld(self, url: str):
    assert lang.detect_lang_by_tld(url) == "ja"

  def test_com_not_ja(self):
    assert lang.detect_lang_by_tld("https://example.com") is None

  def test_none_url(self):
    assert lang.detect_lang_by_tld(None) is None

  def test_empty_string(self):
    assert lang.detect_lang_by_tld("") is None

  def test_no_host(self):
    assert lang.detect_lang_by_tld("not-a-url") is None


class TestNormalize:
  @pytest.mark.parametrize("raw,expected", [
    ("en", "en"),
    ("EN", "en"),
    ("en-US", "en"),
    ("ja-JP", "ja"),
    ("ja_jp", "ja_jp".split("-")[0].lower()[:2] if False else "ja"),  # underscore not split; see below
  ])
  def test_normalize_basic(self, raw: str, expected: str):
    # NOTE: normalize only splits on "-", underscores are not BCP47. See dedicated test below.
    if "_" in raw:
      return
    assert lang.normalize_lang_code(raw) == expected

  def test_underscore_not_split(self):
    # Underscore is not a BCP47 separator; whole token exceeds 2 chars → None.
    assert lang.normalize_lang_code("ja_JP") is None

  def test_non_two_letter_returns_none(self):
    assert lang.normalize_lang_code("eng") is None
    assert lang.normalize_lang_code("x") is None

  def test_empty_and_none(self):
    assert lang.normalize_lang_code(None) is None
    assert lang.normalize_lang_code("") is None


class TestShouldTranslate:
  def test_unknown_source_translates(self):
    # Conservative: if source is unknown, translate (safer than skipping).
    assert lang.should_translate(None, "ja") is True

  def test_same_as_native_does_not_translate(self):
    assert lang.should_translate("ja", "ja") is False

  def test_different_from_native_translates(self):
    assert lang.should_translate("en", "ja") is True


class TestDisplayName:
  def test_known_code(self):
    assert lang.display_name("ja") == "Japanese"
    assert lang.display_name("en") == "English"

  def test_unknown_code_returns_as_is(self):
    # Must not silently force "English" for unregistered codes; otherwise
    # native_lang="fr" would produce English output in summarizer prompts.
    assert lang.display_name("fr") == "fr"
    assert lang.display_name("zh") == "zh"
