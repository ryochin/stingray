"""Tests for call_ollama's retry loop, request payload, and reply parsing.

Callers' tests monkeypatch call_ollama away entirely, so the loop itself —
retrying on non-JSON output and giving up after the last attempt — is covered
here against an httpx.MockTransport instead of a live Ollama.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from llm import _extract_json, call_ollama


def _client(contents: list[str], calls: list[Any]) -> httpx.AsyncClient:
  """A client whose /api/chat replies with the queued message contents."""

  def handler(request: httpx.Request) -> httpx.Response:
    calls.append(json.loads(request.content))
    return httpx.Response(200, json={"message": {"content": contents.pop(0)}})

  return httpx.AsyncClient(base_url="http://ollama", transport=httpx.MockTransport(handler))


def _call(
  contents: list[str],
  calls: list[Any],
  *,
  retries: int = 2,
  options: dict[str, Any] | None = None,
) -> dict[str, Any]:
  async def run() -> dict[str, Any]:
    async with _client(contents, calls) as client:
      return await call_ollama(client, "m", "sys", "user", retries=retries, options=options)

  return asyncio.run(run())


class TestCallOllamaRetries:
  def test_retries_after_non_json_and_returns_the_parsed_reply(self):
    calls: list[Any] = []

    result = _call(["not json", '{"title": "ok"}'], calls)

    assert result == {"title": "ok"}
    assert len(calls) == 2
    # The retry shows the model its bad reply and asks again.
    assert calls[1]["messages"][2] == {"role": "assistant", "content": "not json"}

  def test_raises_the_last_parse_error_once_attempts_run_out(self):
    calls: list[Any] = []

    with pytest.raises(ValueError, match="third"):
      _call(["first", "second", "third"], calls)

    assert len(calls) == 3

  def test_negative_retries_raise_without_calling_the_llm(self):
    calls: list[Any] = []

    with pytest.raises(ValueError, match="no LLM attempt"):
      _call([], calls, retries=-1)

    assert calls == []


def test_options_are_sent_in_the_payload() -> None:
  calls: list[Any] = []

  _call(['{"a": 1}'], calls, options={"num_ctx": 8192})

  assert calls[0]["options"] == {"num_ctx": 8192}


@pytest.mark.parametrize(
  ("content", "expected"),
  [
    # Nested dicts: without the fence / list handling, the fallback regex would
    # still find a dict, but only the innermost one.
    ('```json\n{"a": {"b": {"c": 1}}}\n```', {"a": {"b": {"c": 1}}}),
    ('```json\n{"a": {"b": {"c": 1}}}', {"a": {"b": {"c": 1}}}),
    ('[1, {"a": {"b": {"c": 1}}}, {"d": 2}]', {"a": {"b": {"c": 1}}}),
    ('Sure, here it is: {"a": {"b": 1}} Hope it helps.', {"a": {"b": 1}}),
    ("[1, 2]", None),
    ('Sure: {"a": } oops', None),
  ],
  ids=[
    "fenced",
    "unclosed-fence",
    "list-with-dict",
    "embedded",
    "list-without-dict",
    "bad-embedded",
  ],
)
def test_extract_json(content: str, expected: dict[str, Any] | None) -> None:
  if expected is None:
    with pytest.raises(ValueError, match="invalid JSON"):
      _extract_json(content)
  else:
    assert _extract_json(content) == expected
