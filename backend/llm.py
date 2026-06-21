"""Shared Ollama LLM client."""

import json
import re
from typing import Any, cast

import httpx

import log


def _extract_json(content: str) -> dict[str, Any]:
  """Extract JSON from LLM response, handling markdown fences and embedded JSON."""
  content = content.strip()

  # Strip markdown fences if present
  if content.startswith("```"):
    content = content.split("\n", 1)[1] if "\n" in content else content[3:]
    if content.endswith("```"):
      content = content[:-3]
    content = content.strip()

  def _first_dict(value: object) -> dict[str, Any] | None:
    if isinstance(value, dict):
      return cast(dict[str, Any], value)
    if isinstance(value, list):
      for item in cast(list[object], value):
        if isinstance(item, dict):
          return cast(dict[str, Any], item)
    return None

  # Direct parse
  try:
    found = _first_dict(json.loads(content))
    if found is not None:
      return found
  except json.JSONDecodeError:
    pass

  # Try to find JSON object embedded in text
  match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", content)
  if match:
    try:
      found = _first_dict(json.loads(match.group()))
      if found is not None:
        return found
    except json.JSONDecodeError:
      pass

  raise ValueError(f"LLM returned invalid JSON: {content[:200]}")


async def call_ollama(
  client: httpx.AsyncClient,
  model: str,
  system_prompt: str,
  user_prompt: str,
  *,
  retries: int = 2,
  options: dict[str, Any] | None = None,
) -> dict[str, Any]:
  """Send a chat completion request to Ollama and return parsed JSON."""
  last_error = None
  messages = [
    {"role": "system", "content": system_prompt},
    {"role": "user", "content": user_prompt},
  ]
  for attempt in range(1 + retries):
    payload: dict[str, Any] = {
      "model": model,
      "messages": messages,
      "stream": False,
      "format": "json",
    }
    if options:
      payload["options"] = options
    resp = await client.post("/api/chat", json=payload)
    resp.raise_for_status()
    body: Any = resp.json()
    content = str(body["message"]["content"]).strip()

    try:
      return _extract_json(content)
    except ValueError as e:
      last_error = e
      log.warn(f"LLM returned non-JSON (attempt {attempt + 1}/{1 + retries}):")
      for line in content[:500].split("\n"):
        log.warn(f"  | {line}")
      # Add conversation history so model learns from its mistake
      messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
        {"role": "assistant", "content": content[:200]},
        {"role": "user", "content": "Wrong. Output ONLY a valid JSON object. No text, no explanation, no markdown. Just {\"key\": \"value\"}."},
      ]

  raise last_error  # type: ignore[misc]
