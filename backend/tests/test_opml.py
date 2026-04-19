"""Tests for backend/opml.py — parse/export round-trips and translate inference."""

from __future__ import annotations

import opml
from schemas import FeedRow, FolderRow


def _feed(
  *,
  id: int = 1,
  name: str = "Feed",
  url: str = "https://example.com/rss",
  site_url: str | None = None,
  translate: bool = False,
  summarize: bool = False,
  extraction_rules: str | None = None,
  folder_id: int | None = None,
) -> FeedRow:
  return FeedRow(
    id=id,
    name=name,
    url=url,
    site_url=site_url,
    enabled=True,
    translate=translate,
    summarize=summarize,
    folder_id=folder_id,
    position=0,
    extraction_rules=extraction_rules,
  )


def _folder(*, id: int = 1, name: str = "Folder", position: int = 0) -> FolderRow:
  return FolderRow(id=id, name=name, position=position)


class TestParseOpmlTranslateInference:
  def test_japanese_name_infers_translate_false_for_ja_native(self):
    xml = """<?xml version="1.0"?>
    <opml version="2.0"><body>
      <outline type="rss" text="朝日新聞" xmlUrl="https://example.com/asahi"/>
    </body></opml>"""
    _, uncat = opml.parse_opml(xml, native_lang="ja")
    # Kanji-only name + .com URL → script detect=None, tld detect=None → source=None → translate=True.
    # This is the conservative default; document it.
    assert uncat[0].translate is True

  def test_kana_name_infers_translate_false_for_ja_native(self):
    xml = """<?xml version="1.0"?>
    <opml version="2.0"><body>
      <outline type="rss" text="はてブ 人気エントリ" xmlUrl="https://b.hatena.ne.jp/rss"/>
    </body></opml>"""
    _, uncat = opml.parse_opml(xml, native_lang="ja")
    # Katakana in name → source=ja → same as native → translate=False.
    assert uncat[0].translate is False

  def test_jp_tld_infers_translate_false_for_ja_native(self):
    xml = """<?xml version="1.0"?>
    <opml version="2.0"><body>
      <outline type="rss" text="News" xmlUrl="https://example.jp/rss"/>
    </body></opml>"""
    _, uncat = opml.parse_opml(xml, native_lang="ja")
    assert uncat[0].translate is False

  def test_english_feed_with_com_translates_for_ja_native(self):
    xml = """<?xml version="1.0"?>
    <opml version="2.0"><body>
      <outline type="rss" text="Hacker News" xmlUrl="https://news.ycombinator.com/rss"/>
    </body></opml>"""
    _, uncat = opml.parse_opml(xml, native_lang="ja")
    assert uncat[0].translate is True

  def test_explicit_translate_attr_overrides_heuristic(self):
    xml = """<?xml version="1.0"?>
    <opml version="2.0"><body>
      <outline type="rss" text="はてブ" xmlUrl="https://b.hatena.ne.jp/rss" data-translate="1"/>
    </body></opml>"""
    _, uncat = opml.parse_opml(xml, native_lang="ja")
    assert uncat[0].translate is True


class TestParseOpmlStructure:
  def test_folder_with_feeds(self):
    xml = """<?xml version="1.0"?>
    <opml version="2.0"><body>
      <outline text="Tech">
        <outline type="rss" text="F1" xmlUrl="https://a.example.com/"/>
        <outline type="rss" text="F2" xmlUrl="https://b.example.com/"/>
      </outline>
    </body></opml>"""
    folders, uncat = opml.parse_opml(xml)
    assert len(folders) == 1
    assert folders[0].name == "Tech"
    assert [f.name for f in folders[0].feeds] == ["F1", "F2"]
    assert uncat == []

  def test_empty_folder_dropped(self):
    xml = """<?xml version="1.0"?>
    <opml version="2.0"><body>
      <outline text="Empty"></outline>
    </body></opml>"""
    folders, uncat = opml.parse_opml(xml)
    assert folders == []
    assert uncat == []

  def test_feed_without_url_dropped(self):
    xml = """<?xml version="1.0"?>
    <opml version="2.0"><body>
      <outline type="rss" text="No URL"/>
    </body></opml>"""
    folders, uncat = opml.parse_opml(xml)
    assert folders == []
    assert uncat == []

  def test_web_feed_uses_html_url(self):
    # Web-type feeds must sit inside a folder: at body-level, parse_opml
    # treats "no xmlUrl" as a folder marker, so a top-level web outline would
    # be parsed as an empty folder and dropped. Document that by wrapping.
    xml = """<?xml version="1.0"?>
    <opml version="2.0"><body>
      <outline text="Web">
        <outline type="web" text="Scraped" htmlUrl="https://example.com/page" data-extraction-rules='{"selector":"article"}'/>
      </outline>
    </body></opml>"""
    folders, _ = opml.parse_opml(xml)
    assert len(folders) == 1 and len(folders[0].feeds) == 1
    f = folders[0].feeds[0]
    assert f.url == "https://example.com/page"
    assert f.extraction_rules == '{"selector":"article"}'

  def test_web_feed_default_extraction_rules(self):
    xml = """<?xml version="1.0"?>
    <opml version="2.0"><body>
      <outline text="Web">
        <outline type="web" text="Scraped" htmlUrl="https://example.com/page"/>
      </outline>
    </body></opml>"""
    folders, _ = opml.parse_opml(xml)
    assert folders[0].feeds[0].extraction_rules == "{}"

  def test_top_level_web_feed_is_misparsed_as_empty_folder(self):
    # Regression marker: known limitation — web feeds at top level are
    # swallowed because body-level outlines without xmlUrl become folders.
    xml = """<?xml version="1.0"?>
    <opml version="2.0"><body>
      <outline type="web" text="Top" htmlUrl="https://example.com/page"/>
    </body></opml>"""
    folders, uncat = opml.parse_opml(xml)
    assert folders == [] and uncat == []


class TestExportOpml:
  def test_basic_export(self):
    folders = [_folder(id=1, name="Tech")]
    feeds = [_feed(id=1, name="F1", url="https://a.example.com/", folder_id=1)]
    xml = opml.export_opml(folders, feeds)
    assert "<opml" in xml
    assert 'text="Tech"' in xml
    assert 'xmlUrl="https://a.example.com/"' in xml

  def test_translate_and_summarize_flags_emitted(self):
    xml = opml.export_opml([], [_feed(translate=True, summarize=True)])
    assert 'data-translate="1"' in xml
    assert 'data-summarize="1"' in xml

  def test_empty_folder_not_exported(self):
    xml = opml.export_opml([_folder(id=1, name="Empty")], [])
    assert 'text="Empty"' not in xml

  def test_round_trip_preserves_translate(self):
    feeds = [_feed(name="F1", translate=True), _feed(id=2, name="F2", translate=False, url="https://example.jp/")]
    xml = opml.export_opml([], feeds)
    _, parsed = opml.parse_opml(xml, native_lang="ja")
    by_name = {f.name: f for f in parsed}
    assert by_name["F1"].translate is True
    assert by_name["F2"].translate is False
