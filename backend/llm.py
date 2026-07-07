"""Shared Ollama LLM client."""

import json
import re
from typing import Any, cast

import httpx

import log

# Matches a maximal run of byte-fallback escapes emitted by SentencePiece-based
# models (e.g. gemma) when a rare character cannot be decoded back to UTF-8, e.g.
# "<0xE4><0x91><0x93>" for U+4453. Runs are decoded together so multi-byte and
# multi-character sequences are reassembled correctly.
_BYTE_FALLBACK_RE = re.compile(r"(?:<0x([0-9A-Fa-f]{2})>)+")


def sanitize_byte_fallback(text: str) -> str:
  """Reassemble literal ``<0xNN>`` byte-fallback runs into their UTF-8 chars."""
  if "<0x" not in text:
    return text

  def _replace(match: re.Match[str]) -> str:
    hex_bytes = re.findall(r"<0x([0-9A-Fa-f]{2})>", match.group())
    raw = bytes(int(h, 16) for h in hex_bytes)
    # Byte-fallback only encodes characters outside the model's vocabulary, i.e.
    # non-ASCII ones. A pure-ASCII run is therefore not an artifact (e.g. a
    # literal "<0x41>" in a CSS selector) and is left untouched.
    if all(b < 0x80 for b in raw):
      return match.group()
    try:
      return raw.decode("utf-8")
    except UnicodeDecodeError:
      # Not valid UTF-8 (truncated or malformed run): keep the original literal
      # rather than replacing it with U+FFFD, which would be irreversible.
      return match.group()

  return _BYTE_FALLBACK_RE.sub(_replace, text)


def _sanitize_value(value: Any) -> Any:
  """Recursively sanitize byte-fallback artifacts in strings within JSON data."""
  if isinstance(value, str):
    return sanitize_byte_fallback(value)
  if isinstance(value, dict):
    return {k: _sanitize_value(v) for k, v in cast(dict[str, Any], value).items()}
  if isinstance(value, list):
    return [_sanitize_value(v) for v in cast(list[Any], value)]
  return value


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
      return _sanitize_value(_extract_json(content))
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
