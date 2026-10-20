"""Shared LLM stub: a fake for summarizer.call_ollama.

Patch it where summarizer looks it up:
  monkeypatch.setattr(summarizer, "call_ollama", FakeOllama())
"""

from __future__ import annotations

from typing import Any

import httpx


class FakeOllama:
  """Records calls and returns a canned reply (or raises) per article title."""

  def __init__(self, reply: dict[str, Any] | None = None) -> None:
    self.reply: dict[str, Any] = (
      reply
      if reply is not None
      else {
        "title_translated": "T",
        "summary": "S",
        "content_translated": "C",
      }
    )
    self.prompts: list[str] = []
    self.user_prompts: list[str] = []
    self.fail_titles: set[str] = set()

  async def __call__(
    self,
    client: httpx.AsyncClient,
    model: str,
    system_prompt: str,
    user_prompt: str,
  ) -> dict[str, Any]:
    self.prompts.append(system_prompt)
    self.user_prompts.append(user_prompt)
    for title in self.fail_titles:
      if user_prompt.startswith(f"Title: {title}\n"):
        raise RuntimeError("boom")
    return self.reply
