"""Tests for scraper.validate_extraction_rules.

Shared validation for the PATCH /rules endpoint and LLM selector inference:
allowed keys only, required item/title/link, string values, length cap, and
CSS syntax checking, single-valued attributes, and the "_self" sentinel
(link only).
"""

from __future__ import annotations

import pytest
from bs4.builder import HTMLTreeBuilder

from scraper import MAX_RULE_VALUE_LEN, validate_extraction_rules


class TestValidateExtractionRules:
  def test_minimal_valid_rules_pass(self):
    rules = validate_extraction_rules({"item": "li.entry", "title": "a", "link": "a"})
    assert rules == {"item": "li.entry", "title": "a", "link": "a"}

  def test_optional_fields_are_kept(self):
    raw = {
      "item": "li",
      "title": "h2",
      "link": "a",
      "link_attr": "href",
      "date": "time",
      "date_attr": "datetime",
      "thumbnail": "img",
      "thumbnail_attr": "src",
    }
    assert validate_extraction_rules(raw) == raw

  def test_values_are_trimmed_and_empty_dropped(self):
    rules = validate_extraction_rules({"item": " li ", "title": "a", "link": "a", "date": "  "})
    assert rules["item"] == "li"
    assert "date" not in rules  # blank optional value is dropped

  def test_link_self_sentinel_is_allowed(self):
    rules = validate_extraction_rules({"item": "a.card", "title": "span", "link": "_self"})
    assert rules["link"] == "_self"

  def test_non_dict_rejected(self):
    with pytest.raises(ValueError):
      validate_extraction_rules(["item", "title"])

  def test_unknown_key_rejected(self):
    with pytest.raises(ValueError, match="Unknown"):
      validate_extraction_rules({"item": "li", "title": "a", "link": "a", "bogus": "x"})

  def test_non_string_value_rejected(self):
    with pytest.raises(ValueError, match="must be a string"):
      validate_extraction_rules({"item": "li", "title": "a", "link": 123})

  def test_null_optional_is_treated_as_omitted(self):
    # LLMs fill optional fields with null instead of omitting them; drop, not reject.
    rules = validate_extraction_rules({"item": "li", "title": "a", "link": "a", "thumbnail": None})
    assert rules == {"item": "li", "title": "a", "link": "a"}

  def test_null_required_field_still_rejected(self):
    with pytest.raises(ValueError, match="required"):
      validate_extraction_rules({"item": "li", "title": "a", "link": None})

  @pytest.mark.parametrize("missing", ["item", "title", "link"])
  def test_missing_required_field_rejected(self, missing: str):
    raw = {"item": "li", "title": "a", "link": "a"}
    del raw[missing]
    with pytest.raises(ValueError, match="required"):
      validate_extraction_rules(raw)

  def test_overlong_value_rejected(self):
    raw = {"item": "li", "title": "a", "link": "x" * (MAX_RULE_VALUE_LEN + 1)}
    with pytest.raises(ValueError, match="too long"):
      validate_extraction_rules(raw)

  def test_invalid_css_selector_rejected(self):
    with pytest.raises(ValueError, match="Invalid CSS selector"):
      validate_extraction_rules({"item": "li[", "title": "a", "link": "a"})

  def test_single_relative_has_passes(self):
    rules = validate_extraction_rules({"item": "li:has(> a)", "title": "a", "link": "a"})
    assert rules["item"] == "li:has(> a)"

  @pytest.mark.parametrize("selector", ["li:has(a img)", "li:has(:has(a))"])
  def test_complex_or_nested_has_rejected(self, selector: str):
    # soupsieve 3.0 made :has() strict by default; this pins that behavior so a
    # dependency change that flips it shows up here.
    with pytest.raises(ValueError, match="Invalid CSS selector"):
      validate_extraction_rules({"item": selector, "title": "a", "link": "a"})

  @pytest.mark.parametrize("key", ["link_attr", "date_attr", "thumbnail_attr"])
  @pytest.mark.parametrize("attr", ["class", " rel "])
  def test_multi_valued_attribute_rejected(self, key: str, attr: str):
    # bs4 parses these into lists, which attr_str refuses at fetch time.
    with pytest.raises(ValueError, match="multi-valued"):
      validate_extraction_rules({"item": "li", "title": "a", "link": "a", key: attr})

  def test_every_bs4_multi_valued_attribute_rejected(self):
    # Guards the union: tag-specific ones like `headers` (<td>) count for every tag.
    for attrs in HTMLTreeBuilder.DEFAULT_CDATA_LIST_ATTRIBUTES.values():
      for attr in attrs:
        with pytest.raises(ValueError, match="multi-valued"):
          validate_extraction_rules({"item": "li", "title": "a", "link": "a", "link_attr": attr})

  def test_single_valued_data_attribute_passes(self):
    raw = {
      "item": "li",
      "title": "a",
      "link": "a",
      "thumbnail": "img",
      "thumbnail_attr": "data-src",
    }
    assert validate_extraction_rules(raw) == raw

  @pytest.mark.parametrize("key", ["item", "title", "date", "thumbnail"])
  def test_self_sentinel_outside_link_rejected(self, key: str):
    raw = {"item": "li", "title": "a", "link": "a", key: " _self "}
    with pytest.raises(ValueError, match="only valid for 'link'"):
      validate_extraction_rules(raw)
