"""Unit tests for confining debug `file://` feed URLs to DEBUG_FEEDS_DIR."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import app
import feeds

RSS: str = (
  '<?xml version="1.0"?><rss version="2.0"><channel><title>Local</title>'
  "<link>https://example.com/</link>"
  "<item><title>a</title><link>https://example.com/a</link></item>"
  "</channel></rss>"
)


@pytest.fixture
def feeds_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
  base: Path = tmp_path / "debug-feeds"
  base.mkdir()
  (base / "feed.xml").write_text(RSS, encoding="utf-8")
  monkeypatch.setattr(feeds, "DEBUG_FEEDS_DIR", base)
  return base


def test_relative_path_reads_inside_the_dir(feeds_dir: Path):
  assert feeds.read_file_url("file://feed.xml") == RSS
  assert feeds.read_file_url("file://./feed.xml") == RSS


def test_absolute_path_inside_the_dir_is_allowed(feeds_dir: Path):
  assert feeds.read_file_url(f"file://{feeds_dir}/feed.xml") == RSS


def test_dotdot_escape_is_rejected(feeds_dir: Path):
  (feeds_dir.parent / "secret.txt").write_text("secret", encoding="utf-8")
  with pytest.raises(ValueError):
    feeds.read_file_url("file://../secret.txt")


def test_absolute_path_outside_the_dir_is_rejected(feeds_dir: Path):
  secret: Path = feeds_dir.parent / "secret.txt"
  secret.write_text("secret", encoding="utf-8")
  with pytest.raises(ValueError):
    feeds.read_file_url(f"file://{secret}")


def test_symlink_pointing_outside_is_rejected(feeds_dir: Path):
  secret: Path = feeds_dir.parent / "secret.txt"
  secret.write_text("secret", encoding="utf-8")
  (feeds_dir / "link.xml").symlink_to(secret)
  with pytest.raises(ValueError):
    feeds.read_file_url("file://link.xml")


def test_sibling_dir_sharing_the_prefix_is_rejected(feeds_dir: Path):
  # A string prefix check would let `debug-feeds-other` through.
  sibling: Path = feeds_dir.parent / f"{feeds_dir.name}-other"
  sibling.mkdir()
  (sibling / "x.xml").write_text(RSS, encoding="utf-8")
  with pytest.raises(ValueError):
    feeds.read_file_url(f"file://{sibling}/x.xml")


def test_probe_reads_a_file_inside_the_dir(feeds_dir: Path):
  result: app.ProbeResult = asyncio.run(
    app._probe_feed("file://feed.xml", native_lang="en")
  )
  assert result.title == "Local"


def test_probe_returns_empty_result_for_a_path_outside(feeds_dir: Path):
  secret: Path = feeds_dir.parent / "secret.xml"
  secret.write_text(RSS, encoding="utf-8")
  result: app.ProbeResult = asyncio.run(
    app._probe_feed(f"file://{secret}", native_lang="en")
  )
  assert result == app.ProbeResult()
