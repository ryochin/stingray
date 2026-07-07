"""Tests for byte-fallback artifact sanitization in the shared LLM client.

SentencePiece-based models (e.g. gemma) may emit literal ``<0xNN>`` escapes when
a rare character fails to decode back to UTF-8. The sanitizer reassembles those
runs into their original characters before the JSON is returned to callers.
"""

from __future__ import annotations

import json
from typing import Any

from llm import sanitize_byte_fallback, _sanitize_value, call_ollama


class TestSanitizeByteFallback:
  def test_single_character(self):
    # <0xE4><0x91><0x93> is the UTF-8 encoding of U+4453 (䑓).
    assert sanitize_byte_fallback("<0xE4><0x91><0x93>") == "䑓"

  def test_consecutive_run_multiple_characters(self):
    # Full-width space (U+3000) followed by U+4453, decoded as one run.
    assert sanitize_byte_fallback("<0xE3><0x80><0x80><0xE4><0x91><0x93>") == "　䑓"

  def test_mixed_with_surrounding_text(self):
    text = "。<0xE3><0x80><0x80><0xE4><0x91><0x93>原蓉子"
    assert sanitize_byte_fallback(text) == "。　䑓原蓉子"

  def test_separate_runs_decoded_independently(self):
    # Two distinct runs separated by a normal character.
    text = "<0xE4><0x91><0x93>x<0xE3><0x80><0x80>"
    assert sanitize_byte_fallback(text) == "䑓x　"

  def test_lowercase_hex(self):
    assert sanitize_byte_fallback("<0xe4><0x91><0x93>") == "䑓"

  def test_plain_string_unchanged(self):
    assert sanitize_byte_fallback("plain タイトル") == "plain タイトル"

  def test_empty_string_unchanged(self):
    assert sanitize_byte_fallback("") == ""

  def test_invalid_byte_sequence_preserved(self):
    # A lone continuation byte is not valid UTF-8; keep the literal rather than
    # irreversibly replacing it with U+FFFD.
    assert sanitize_byte_fallback("<0x91>") == "<0x91>"

  def test_truncated_multibyte_run_preserved(self):
    # First byte of a 3-byte sequence with the rest missing: not decodable, so
    # the original literal is preserved.
    assert sanitize_byte_fallback("<0xE4>") == "<0xE4>"

  def test_ascii_byte_run_not_converted(self):
    # Pure-ASCII runs are never byte-fallback artifacts and stay verbatim.
    assert sanitize_byte_fallback("<0x41>") == "<0x41>"


class TestSanitizeValue:
  def test_recurses_into_nested_structures(self):
    data: dict[str, Any] = {
      "title": "<0xE4><0x91><0x93>",
      "tags": ["ok", "<0xE3><0x80><0x80>"],
      "nested": {"summary": "a<0xE4><0x91><0x93>b"},
      "count": 3,
    }
    assert _sanitize_value(data) == {
      "title": "䑓",
      "tags": ["ok", "　"],
      "nested": {"summary": "a䑓b"},
      "count": 3,
    }


class _FakeResponse:
  def __init__(self, content: str):
    self._content = content

  def raise_for_status(self) -> None:
    return None

  def json(self) -> dict[str, Any]:
    return {"message": {"content": self._content}}


class _FakeClient:
  def __init__(self, content: str):
    self._content = content

  async def post(self, _url: str, **_kwargs: Any) -> _FakeResponse:
    return _FakeResponse(self._content)


def test_call_ollama_sanitizes_return_value():
  import asyncio

  payload = json.dumps({"title_translated": "。<0xE3><0x80><0x80><0xE4><0x91><0x93>原蓉子"})
  client = _FakeClient(payload)

  result = asyncio.run(
    call_ollama(client, "gemma", "sys", "user")  # type: ignore[arg-type]
  )
  assert result == {"title_translated": "。　䑓原蓉子"}
