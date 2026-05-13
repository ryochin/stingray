"""Strip well-known tracking parameters from URLs.

Pure, dependency-free. Used at ingestion time (feeds + scraper) so that
articles land in the DB with clean, canonical URLs. Cleans up the link
display and lets the existing `articles.url` PK collapse duplicates
that only differ by tracker.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


# Any query parameter whose lowercased name starts with one of these is
# dropped. Kept narrow on purpose — only families that are unambiguously
# tracking-only and prefix-shaped.
_PREFIXES: tuple[str, ...] = (
  "utm_",  # Google Analytics
  "pk_",  # Piwik / Matomo (legacy)
  "mtm_",  # Matomo (new)
  "stm_",  # Same family
  "hsa_",  # HubSpot ads
  "oly_",  # Omeda
)


# Exact-match (case-insensitive) tracker names. Curated conservatively;
# prefer exact entries over a broad prefix when the family includes
# names that could collide with legitimate params (e.g. HubSpot's
# `__hs*` family, ConvertKit's `ck_*` namespace).
_EXACT: frozenset[str] = frozenset(
  {
    # Click IDs
    "fbclid",  # Facebook
    "gclid",  # Google Ads
    "dclid",  # Display & Video 360
    "gclsrc",  # Google Ads source
    "gbraid",  # Google iOS click ID
    "wbraid",  # Google iOS click ID (web)
    "msclkid",  # Microsoft / Bing Ads
    "yclid",  # Yandex
    "ysclid",  # Yandex (new)
    "twclid",  # X / Twitter
    "ttclid",  # TikTok
    "li_fat_id",  # LinkedIn
    # Social share IDs
    "igshid",  # Instagram share
    "igsh",  # Instagram share (short)
    "mibextid",  # Meta / Facebook share
    "srsltid",  # Google search result tracking
    # Email / marketing automation
    "mc_cid",  # Mailchimp campaign
    "mc_eid",  # Mailchimp email
    "mkt_tok",  # Marketo
    "vero_id",
    "vero_conv",
    "__s",  # Drip
    "_kx",  # Klaviyo
    "ck_subscriber_id",  # ConvertKit / Kit (narrowed from prefix ck_)
    # HubSpot — `_hs` prefix would miss the `__hs*` family, so list each.
    "_hsenc",
    "_hsmi",
    "__hssc",
    "__hstc",
    "__hsfp",
    "hsctatracking",
    # Adobe (Omniture / Marketing Cloud)
    "s_kwcid",
    "ef_id",
    # Other
    "_openstat",
    "ref_src",  # Twitter referral
    "ref_url",  # Generic referral
  }
)


def _is_tracker(name: str) -> bool:
  n = name.lower()
  if n in _EXACT:
    return True
  return any(n.startswith(p) for p in _PREFIXES)


def clean_url(url: str) -> str:
  """Return `url` with well-known tracking query parameters removed.

  Note: kept parameters round-trip through `urlencode`, which normalizes
  `%20` to `+` and expands `?flag` (no `=`) to `?flag=`. This is
  semantically equivalent and intentional — the canonical form helps
  the `articles.url` PK collapse duplicates.
  """
  parts = urlsplit(url)
  if not parts.query:
    return url
  kept = [
    (k, v)
    for k, v in parse_qsl(parts.query, keep_blank_values=True)
    if not _is_tracker(k)
  ]
  new_query = urlencode(kept, doseq=False)
  return urlunsplit(parts._replace(query=new_query))
