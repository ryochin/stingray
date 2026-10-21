"""Tests for call_ollama's retry loop.

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

from llm import call_ollama


def _client(contents: list[str], calls: list[Any]) -> httpx.AsyncClient:
  """A client whose /api/chat replies with the queued message contents."""

  def handler(request: httpx.Request) -> httpx.Response:
    calls.append(json.loads(request.content))
    return httpx.Response(200, json={"message": {"content": contents.pop(0)}})

  return httpx.AsyncClient(base_url="http://ollama", transport=httpx.MockTransport(handler))


def _call(contents: list[str], calls: list[Any], *, retries: int = 2) -> dict[str, Any]:
  async def run() -> dict[str, Any]:
    async with _client(contents, calls) as client:
      return await call_ollama(client, "m", "sys", "user", retries=retries)

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
