"""Resolve relative URLs in stored article HTML against the article's own URL.

Feed payloads routinely carry markup lifted straight out of the source page,
complete with root-relative links like `href="/bbs/link.cgi?URL=..."`. The base
those were written against is only available while fetching, so the resolution
happens here — once, at ingest — and `articles.content_html` stays a
self-contained fragment that any client can render as-is.
"""

from urllib.parse import urljoin

from bs4 import BeautifulSoup

# Attributes carrying a single URL, per tag. Only attributes that survive
# feedparser's sanitizer are listed: `srcset` and `<iframe>` are stripped
# before the markup ever reaches us, so handling them would be dead code.
_URL_ATTRS: tuple[tuple[str, str], ...] = (
  ("a", "href"),
  ("area", "href"),
  ("img", "src"),
  ("img", "longdesc"),
  ("source", "src"),
  ("video", "src"),
  ("video", "poster"),
  ("audio", "src"),
  ("table", "background"),
  ("td", "background"),
  ("th", "background"),
  ("blockquote", "cite"),
  ("q", "cite"),
  ("ins", "cite"),
  ("del", "cite"),
)

# Values that are already absolute or intentionally document-local. A bare
# `#fragment` points at an anchor inside the article body, which is embedded in
# our own page — rewriting it to the source site would break the jump.
_SKIP_PREFIXES: tuple[str, ...] = (
  "http://",
  "https://",
  "#",
  "mailto:",
  "javascript:",
  "tel:",
  "data:",
)


def absolutize_html_urls(html_text: str, base_url: str) -> str:
  """Rewrite relative URLs in `html_text` against `base_url`.

  Returns the input unchanged when there is nothing to resolve. That matters:
  a BeautifulSoup round-trip normalizes malformed nesting (`<p>A<p>B` becomes
  `<p>A</p><p>B</p>`), so the fragment is only re-serialized when a URL
  actually changed.

  Known limitation: when a URL *does* change, that normalization is applied.
  `html.parser` is not an HTML5 tree builder, so for markup with crossed tags
  it can land on a different tree than a browser would build — see
  `test_malformed_nesting_is_normalized_when_a_url_changes`. Only already
  malformed fragments are affected.
  """
  if not html_text or not base_url:
    return html_text

  soup = BeautifulSoup(html_text, "html.parser")
  changed = False

  for tag_name, attr in _URL_ATTRS:
    for tag in soup.find_all(tag_name):
      value = tag.get(attr)
      if not isinstance(value, str):
        continue
      resolved = _resolve(value, base_url)
      if resolved is not None and resolved != value:
        tag[attr] = resolved
        changed = True

  return str(soup) if changed else html_text


def _resolve(value: str, base_url: str) -> str | None:
  """Absolute form of `value`, or None when it should be left alone."""
  stripped = value.strip()
  # Schemes are case-insensitive: without folding, `HTTPS://…` would slip past
  # and come back lowercased from urljoin — a pointless diff that would force a
  # re-serialization of the whole fragment.
  if not stripped or stripped.lower().startswith(_SKIP_PREFIXES):
    return None
  try:
    return urljoin(base_url, stripped)
  except ValueError:
    # Malformed URLs (`http://[bad`) must not take down the whole fetch.
    return None
