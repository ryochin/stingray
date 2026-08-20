"""Language detection and translation-need heuristics.

Centralizes language-aware decisions that used to be scattered across
`models`, `feeds`, `app`, `opml`, and `summarizer`. Add a new language by
registering a `LangProfile` entry in `PROFILES`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class LangProfile:
  code: str
  name: str
  scripts: tuple[re.Pattern[str], ...] = ()
  tlds: tuple[str, ...] = ()


PROFILES: dict[str, LangProfile] = {
  "ja": LangProfile(
    code="ja",
    name="Japanese",
    scripts=(re.compile(r"[\u3040-\u309F\u30A0-\u30FF]"),),
    tlds=(".jp",),
  ),
  # English is registered for naming only, not for detection: there is no
  # script or TLD that identifies English without also matching many other
  # languages. `display_name` needs this entry so the summarizer prompts read
  # "into English" rather than "into en"; `should_translate` reads the empty
  # `scripts`/`tlds` as "cannot detect this language".
  "en": LangProfile(code="en", name="English"),
}


def detect_lang_by_script(text: str) -> str | None:
  if not text:
    return None
  for code, profile in PROFILES.items():
    for pattern in profile.scripts:
      if pattern.search(text):
        return code
  return None


def detect_lang_by_tld(url: str | None) -> str | None:
  if not url:
    return None
  host = (urlparse(url).hostname or "").lower()
  if not host:
    return None
  for code, profile in PROFILES.items():
    for tld in profile.tlds:
      if host.endswith(tld):
        return code
  return None


def normalize_lang_code(raw: str | None) -> str | None:
  """Normalize a BCP47 tag to a two-letter code, e.g. `en-US` -> `en`."""
  if not raw:
    return None
  code = raw.split("-")[0].strip().lower()
  return code if len(code) == 2 else None


def should_translate(source_lang: str | None, native_lang: str) -> bool:
  """Decide whether a feed in `source_lang` needs translating into `native_lang`.

  When the source language is unknown, fall back on whether the native
  language is detectable at all:

  | source | native | result                                    |
  |--------|--------|-------------------------------------------|
  | `ja`   | `en`   | translate                                 |
  | `en`   | `en`   | skip                                      |
  | `None` | `ja`   | translate (kana/`.jp` would have matched) |
  | `None` | `en`   | skip (English is undetectable)            |
  | `None` | `fr`   | skip (unregistered)                       |

  The asymmetry is deliberate. Assuming "foreign" for an undetectable native
  language burns the LLM on every article of every untagged feed forever,
  while the opposite mistake costs one toggle in the feeds view.
  """
  if source_lang is not None:
    return source_lang != native_lang
  profile = PROFILES.get(native_lang)
  return bool(profile and (profile.scripts or profile.tlds))


def display_name(lang_code: str) -> str:
  # Unregistered codes fall through as-is rather than forcing English,
  # so that e.g. native_lang="fr" does not silently produce English output.
  profile = PROFILES.get(lang_code)
  return profile.name if profile else lang_code
