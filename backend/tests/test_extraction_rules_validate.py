"""Tests for scraper.validate_extraction_rules.

Shared validation for the PATCH /rules endpoint and LLM selector inference:
allowed keys only, required item/title/link, string values, length cap, and
CSS syntax checking (with the "_self" link sentinel exempted).
"""

from __future__ import annotations

import pytest

from scraper import MAX_RULE_VALUE_LEN, validate_extraction_rules


class TestValidateExtractionRules:
  def test_minimal_valid_rules_pass(self):
    rules = validate_extraction_rules({"item": "li.entry", "title": "a", "link": "a"})
    assert rules == {"item": "li.entry", "title": "a", "link": "a"}

  def test_optional_fields_are_kept(self):
    raw = {
      "item": "li", "title": "h2", "link": "a",
      "link_attr": "href", "date": "time", "date_attr": "datetime",
      "thumbnail": "img", "thumbnail_attr": "src",
    }
    assert validate_extraction_rules(raw) == raw

  def test_values_are_trimmed_and_empty_dropped(self):
    rules = validate_extraction_rules(
      {"item": " li ", "title": "a", "link": "a", "date": "  "}
    )
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
    rules = validate_extraction_rules(
      {"item": "li", "title": "a", "link": "a", "thumbnail": None}
    )
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
