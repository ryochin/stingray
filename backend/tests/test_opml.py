"""Tests for backend/opml.py — parse/export round-trips and translate inference."""

from __future__ import annotations

import pytest

import opml
from schemas import FeedRow, FolderRow


def _feed(
  *,
  id: int = 1,
  name: str = "Feed",
  url: str | None = "https://example.com/rss",
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
  # The Japanese feed names below are the subject under test, not placeholders:
  # the inference reads the script of the name, so ASCII would void these cases.
  def test_kanji_only_name_infers_translate_true_for_ja_native(self):
    xml = """<?xml version="1.0"?>
    <opml version="2.0"><body>
      <outline type="rss" text="朝日新聞" xmlUrl="https://example.com/asahi"/>
    </body></opml>"""
    _, uncat = opml.parse_opml(xml, native_lang="ja")
    # Kanji-only name + .com URL → script detect=None, tld detect=None → source=None.
    # Japanese is script-detectable, so "not detected" is read as "not Japanese".
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

  def test_english_feed_with_com_does_not_translate_for_en_native(self):
    # The first-run path for an English speaker: an untagged .com feed must not
    # be queued for English-to-English translation.
    xml = """<?xml version="1.0"?>
    <opml version="2.0"><body>
      <outline type="rss" text="Hacker News" xmlUrl="https://news.ycombinator.com/rss"/>
    </body></opml>"""
    _, uncat = opml.parse_opml(xml, native_lang="en")
    assert uncat[0].translate is False

  def test_jp_tld_infers_translate_true_for_en_native(self):
    xml = """<?xml version="1.0"?>
    <opml version="2.0"><body>
      <outline type="rss" text="News" xmlUrl="https://example.jp/rss"/>
    </body></opml>"""
    _, uncat = opml.parse_opml(xml, native_lang="en")
    assert uncat[0].translate is True

  def test_kana_name_infers_translate_true_for_en_native(self):
    # Katakana in name → source=ja → differs from native en → translate=True.
    xml = """<?xml version="1.0"?>
    <opml version="2.0"><body>
      <outline type="rss" text="はてブ 人気エントリ" xmlUrl="https://example.com/rss"/>
    </body></opml>"""
    _, uncat = opml.parse_opml(xml, native_lang="en")
    assert uncat[0].translate is True

  def test_explicit_translate_attr_overrides_heuristic(self):
    # The .jp URL would infer translate=False for a ja native; data-translate
    # must win over that.
    xml = """<?xml version="1.0"?>
    <opml version="2.0"><body>
      <outline type="rss" text="Hatena Bookmark" xmlUrl="https://b.hatena.ne.jp/rss" data-translate="1"/>
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
    folders, uncat = opml.parse_opml(xml, native_lang="en")
    assert len(folders) == 1
    assert folders[0].name == "Tech"
    assert [f.name for f in folders[0].feeds] == ["F1", "F2"]
    assert uncat == []

  @pytest.mark.parametrize(
    ("attrs", "expected"),
    [
      ('text="  Tech  "', "Tech"),
      ('text="   " title="Tech"', "Tech"),
      ('text="   " title="   "', "Unnamed"),
    ],
  )
  def test_folder_name_is_trimmed_and_falls_through_when_blank(self, attrs: str, expected: str):
    xml = f"""<?xml version="1.0"?>
    <opml version="2.0"><body>
      <outline {attrs}>
        <outline type="rss" text="F1" xmlUrl="https://a.example.com/"/>
      </outline>
    </body></opml>"""
    folders, _ = opml.parse_opml(xml, native_lang="en")
    assert [f.name for f in folders] == [expected]

  def test_empty_folder_dropped(self):
    xml = """<?xml version="1.0"?>
    <opml version="2.0"><body>
      <outline text="Empty"></outline>
    </body></opml>"""
    folders, uncat = opml.parse_opml(xml, native_lang="en")
    assert folders == []
    assert uncat == []

  def test_feed_without_url_dropped(self):
    xml = """<?xml version="1.0"?>
    <opml version="2.0"><body>
      <outline type="rss" text="No URL"/>
    </body></opml>"""
    folders, uncat = opml.parse_opml(xml, native_lang="en")
    assert folders == []
    assert uncat == []

  def test_web_feed_uses_html_url(self):
    # A web-type feed nested in a folder is parsed from its htmlUrl.
    xml = """<?xml version="1.0"?>
    <opml version="2.0"><body>
      <outline text="Web">
        <outline type="web" text="Scraped" htmlUrl="https://example.com/page" data-extraction-rules='{"item":"article","title":"h2","link":"a"}'/>
      </outline>
    </body></opml>"""
    folders, _ = opml.parse_opml(xml, native_lang="en")
    assert len(folders) == 1 and len(folders[0].feeds) == 1
    f = folders[0].feeds[0]
    assert f.url == "https://example.com/page"
    assert f.extraction_rules == '{"item": "article", "title": "h2", "link": "a"}'

  def test_web_feed_default_extraction_rules(self):
    xml = """<?xml version="1.0"?>
    <opml version="2.0"><body>
      <outline text="Web">
        <outline type="web" text="Scraped" htmlUrl="https://example.com/page"/>
      </outline>
    </body></opml>"""
    folders, _ = opml.parse_opml(xml, native_lang="en")
    assert folders[0].feeds[0].extraction_rules == "{}"

  def test_top_level_web_feed_imported_as_uncategorized(self):
    # Folderless web feeds carry htmlUrl without xmlUrl; they must still be
    # recognized as feeds instead of being swallowed as empty folders.
    xml = """<?xml version="1.0"?>
    <opml version="2.0"><body>
      <outline type="web" text="Top" htmlUrl="https://example.com/page"/>
    </body></opml>"""
    folders, uncat = opml.parse_opml(xml, native_lang="en")
    assert folders == []
    assert len(uncat) == 1
    assert uncat[0].url == "https://example.com/page"
    assert uncat[0].extraction_rules == "{}"

  def test_web_typed_outline_with_children_is_folder(self):
    # An outline carrying type="web"/htmlUrl but containing child outlines is
    # a folder, not a leaf feed — its children must still be imported.
    xml = """<?xml version="1.0"?>
    <opml version="2.0"><body>
      <outline type="web" text="Folder" htmlUrl="https://example.com/">
        <outline type="rss" text="Child" xmlUrl="https://example.com/rss"/>
      </outline>
    </body></opml>"""
    folders, uncat = opml.parse_opml(xml, native_lang="en")
    assert uncat == []
    assert len(folders) == 1
    assert folders[0].name == "Folder"
    assert [f.url for f in folders[0].feeds] == ["https://example.com/rss"]


def _import_rules(rules_attr: str, *, feed_type: str = "web") -> str | None:
  """Parse one top-level outline carrying `data-extraction-rules` and return
  what the import would store."""
  url_attr = (
    'htmlUrl="https://example.com/page"'
    if feed_type == "web"
    else 'xmlUrl="https://example.com/rss"'
  )
  xml = f"""<?xml version="1.0"?>
  <opml version="2.0"><body>
    <outline type="{feed_type}" text="W" {url_attr} data-extraction-rules='{rules_attr}'/>
  </body></opml>"""
  _, uncat = opml.parse_opml(xml, native_lang="en")
  assert len(uncat) == 1
  return uncat[0].extraction_rules


class TestParseOpmlExtractionRules:
  def test_valid_rules_are_normalized_like_patch(self):
    # Same normalization as PATCH /rules: values trimmed, null/empty dropped.
    stored = _import_rules('{"item":" .x ","title":".t","link":"_self","date":null,"thumbnail":""}')
    assert stored == '{"item": ".x", "title": ".t", "link": "_self"}'

  @pytest.mark.parametrize(
    "rules_attr",
    [
      pytest.param("{", id="broken-json"),
      pytest.param("[]", id="not-an-object"),
      pytest.param('{"item":".x","title":".t","link":".t","bogus":"1"}', id="unknown-key"),
      pytest.param('{"item":".x","title":".t"}', id="missing-required"),
      pytest.param('{"item":".x[","title":".t","link":".t"}', id="bad-selector"),
      pytest.param("[" * 100_000, id="deep-nesting"),
    ],
  )
  def test_invalid_rules_fall_back_to_unconfigured(
    self, rules_attr: str, capsys: pytest.CaptureFixture[str]
  ):
    # The feed is kept as a web feed with no rules rather than dropped.
    assert _import_rules(rules_attr) == "{}"
    assert "dropped invalid extraction rules for 'W'" in capsys.readouterr().out

  @pytest.mark.parametrize("rules_attr", ["{}", " { } ", ""])
  def test_empty_rules_mean_unconfigured(self, rules_attr: str, capsys: pytest.CaptureFixture[str]):
    assert _import_rules(rules_attr) == "{}"
    assert capsys.readouterr().out == ""

  def test_rss_outline_ignores_rules(self):
    assert _import_rules("{", feed_type="rss") is None


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

  def test_rss_feed_emits_site_url_as_html_url(self):
    # RSS feeds expose both the feed URL (xmlUrl) and the site URL (htmlUrl).
    feed = _feed(url="https://example.com/rss", site_url="https://example.com/")
    xml = opml.export_opml([], [feed])
    assert 'xmlUrl="https://example.com/rss"' in xml
    assert 'htmlUrl="https://example.com/"' in xml

  # Any non-None extraction_rules marks a web feed (incl. "" and the "{}"
  # default a feed carries right after creation), so htmlUrl must stay the
  # scrape target (url) for all of them, never the differing site_url.
  @pytest.mark.parametrize("rules", ["", "{}", '{"item": ".x", "title": ".t", "link": ".t"}'])
  def test_web_feed_html_url_keeps_url_not_site_url(self, rules: str):
    feed = _feed(
      url="https://example.com/articles",
      site_url="https://example.com/",
      extraction_rules=rules,
    )
    xml = opml.export_opml([], [feed])
    assert 'htmlUrl="https://example.com/articles"' in xml
    assert 'htmlUrl="https://example.com/"' not in xml

  def test_url_less_web_feed_falls_back_to_site_url(self):
    # A web feed without a scrape url must still emit htmlUrl from site_url,
    # or it would be silently dropped on re-import.
    feed = _feed(url=None, site_url="https://example.com/", extraction_rules="{}")
    xml = opml.export_opml([], [feed])
    assert 'htmlUrl="https://example.com/"' in xml

  def test_url_less_rss_feed_emits_site_url_as_html_url(self):
    # A url-less RSS feed (legacy/hand-entered) keeps exporting its site_url.
    feed = _feed(url=None, site_url="https://example.com/")
    xml = opml.export_opml([], [feed])
    assert 'htmlUrl="https://example.com/"' in xml
    assert "xmlUrl" not in xml

  def test_round_trip_preserves_translate(self):
    feeds = [
      _feed(name="F1", translate=True),
      _feed(id=2, name="F2", translate=False, url="https://example.jp/"),
    ]
    xml = opml.export_opml([], feeds)
    _, parsed = opml.parse_opml(xml, native_lang="ja")
    by_name = {f.name: f for f in parsed}
    assert by_name["F1"].translate is True
    assert by_name["F2"].translate is False

  def test_round_trip_preserves_top_level_web_feed(self):
    rules = '{"item": ".x", "title": ".t", "link": ".t"}'
    feeds = [_feed(name="W", url="https://example.com/page", extraction_rules=rules)]
    xml = opml.export_opml([], feeds)
    folders, uncat = opml.parse_opml(xml, native_lang="en")
    assert folders == []
    assert len(uncat) == 1
    assert uncat[0].url == "https://example.com/page"
    assert uncat[0].extraction_rules == rules

  def test_round_trip_preserves_web_scrape_url_distinct_from_site_url(self):
    # When a web feed's scrape target differs from its site_url, the round-trip
    # must keep scraping the original page rather than the site root.
    rules = '{"item": ".x", "title": ".t", "link": ".t"}'
    feeds = [
      _feed(
        name="W",
        url="https://example.com/articles",
        site_url="https://example.com/",
        extraction_rules=rules,
      ),
    ]
    xml = opml.export_opml([], feeds)
    _, uncat = opml.parse_opml(xml, native_lang="en")
    assert len(uncat) == 1
    assert uncat[0].url == "https://example.com/articles"
    assert uncat[0].extraction_rules == rules
