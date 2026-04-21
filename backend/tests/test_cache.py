"""Tests for the feed response cache (file-based, atomic write)."""

from __future__ import annotations

from pathlib import Path

import pytest

import cache


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
  """Point cache.configure at a tmp dir and restore afterwards."""
  original_cache = cache._cache_dir
  original_feed = cache._feed_cache_dir
  cache.configure(tmp_path)
  yield tmp_path
  # Restore module state so other tests aren't affected.
  cache._cache_dir = original_cache
  cache._feed_cache_dir = original_feed


class TestFeedCache:
  def test_miss_returns_none(self, cache_dir: Path):
    assert cache.load_feed_cache("https://example.com/rss") is None

  def test_save_then_load_roundtrip(self, cache_dir: Path):
    cache.save_feed_cache(
      "https://example.com/rss",
      etag='"abc"',
      last_modified="Mon, 01 Jan 2024 00:00:00 GMT",
      body="<rss>body</rss>",
    )
    loaded = cache.load_feed_cache("https://example.com/rss")
    assert loaded is not None
    assert loaded["url"] == "https://example.com/rss"
    assert loaded["etag"] == '"abc"'
    assert loaded["last_modified"] == "Mon, 01 Jan 2024 00:00:00 GMT"
    assert loaded["body"] == "<rss>body</rss>"
    assert "content_hash" in loaded

  def test_different_urls_are_isolated(self, cache_dir: Path):
    cache.save_feed_cache("https://a.example.com/", None, None, "A")
    cache.save_feed_cache("https://b.example.com/", None, None, "B")
    a = cache.load_feed_cache("https://a.example.com/")
    b = cache.load_feed_cache("https://b.example.com/")
    assert a is not None and a["body"] == "A"
    assert b is not None and b["body"] == "B"

  def test_overwrite(self, cache_dir: Path):
    url = "https://example.com/rss"
    cache.save_feed_cache(url, None, None, "v1")
    cache.save_feed_cache(url, None, None, "v2")
    loaded = cache.load_feed_cache(url)
    assert loaded is not None and loaded["body"] == "v2"

  def test_corrupted_cache_returns_none(self, cache_dir: Path):
    url = "https://example.com/rss"
    cache.save_feed_cache(url, None, None, "ok")
    # Directly corrupt the file on disk.
    path = cache._feed_cache_dir / (cache._feed_key(url) + ".json")
    path.write_text("{not valid json", encoding="utf-8")
    assert cache.load_feed_cache(url) is None

  def test_unicode_body_roundtrip(self, cache_dir: Path):
    cache.save_feed_cache("https://example.jp/", None, None, "日本語🎌")
    loaded = cache.load_feed_cache("https://example.jp/")
    assert loaded is not None
    assert loaded["body"] == "日本語🎌"

  def test_content_hash_deterministic(self, cache_dir: Path):
    cache.save_feed_cache("https://a/", None, None, "same-body")
    cache.save_feed_cache("https://b/", None, None, "same-body")
    a = cache.load_feed_cache("https://a/")
    b = cache.load_feed_cache("https://b/")
    assert a is not None and b is not None
    assert a["content_hash"] == b["content_hash"]


class TestAtomicWrite:
  def test_no_tmp_files_left_behind(self, cache_dir: Path):
    cache.save_feed_cache("https://example.com/", None, None, "body")
    tmp_files = list(cache._feed_cache_dir.glob("*.tmp"))
    assert tmp_files == []


class TestPurgeFeedCache:
  def test_returns_zero_when_cache_empty(self, cache_dir: Path):
    assert cache.purge_feed_cache() == 0

  def test_removes_every_cached_body(self, cache_dir: Path):
    cache.save_feed_cache("https://a/", None, None, "A")
    cache.save_feed_cache("https://b/", None, None, "B")
    assert cache.purge_feed_cache() == 2
    assert cache.load_feed_cache("https://a/") is None
    assert cache.load_feed_cache("https://b/") is None

  def test_purge_is_idempotent(self, cache_dir: Path):
    cache.save_feed_cache("https://a/", None, None, "A")
    cache.purge_feed_cache()
    assert cache.purge_feed_cache() == 0
