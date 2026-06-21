"""LLM-based CSS selector inference for non-feed web pages.

Given the HTML of a page that lists articles but exposes no RSS/Atom feed, ask
the LLM to produce `extraction_rules` (CSS selectors), then validate them by
actually running the existing scraper. The result feeds the rules editor as a
preview — it is never persisted automatically.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup, Comment

from llm import call_ollama
from models import Article
from scraper import parse_web_page, validate_extraction_rules

# Tags whose content never helps identify the article-list structure. The
# layout tags (nav/aside/footer) wrap menus and chrome, not the list. `header`
# is intentionally excluded: article cards commonly wrap their title/link in a
# nested <header>, so stripping it would erase the very structure we infer.
_NOISE_TAGS = (
  "script", "style", "svg", "noscript", "template", "iframe",
  "nav", "aside", "footer",
)
# Attributes worth keeping for selector inference; everything else is stripped
# to cut tokens (inline styles, event handlers, framework data-* noise).
_KEEP_ATTRS = frozenset({"class", "id", "href", "datetime", "src", "rel", "itemprop"})
# class/id substrings marking carousels/sliders: prominent repeating blocks that
# are not the article list and otherwise mislead the model.
_NOISE_CLASS_HINTS = ("swiper", "carousel", "slider")

# Number of extracted articles returned as a preview to the UI.
_SAMPLE_LIMIT = 5

INFER_SYSTEM_PROMPT = """\
You are a web scraping expert. Given the HTML of a page that lists articles
(a blog index, news list, or similar), identify the CSS selectors that extract
each ARTICLE in the page's MAIN article list.

CRITICAL: Target the main list of individual articles/blog posts ONLY. Do NOT
select navigation menus, image carousels/sliders, sidebars, "ranking" or
"recommended/related" widgets, tag clouds, or lists of companies/categories.
Each "item" must correspond to one article whose link points to an individual
article page (not a category, tag, or company index).

Return ONLY a JSON object with these fields:

- "item": CSS selector matching each repeating article container (required).
- "title": CSS selector, relative to item, for the article title (required).
- "link": CSS selector, relative to item, for the link to the article (required).
  Use the literal "_self" if the item element itself is the <a> link.
- "link_attr": attribute holding the URL (default "href"). Optional.
- "date": CSS selector, relative to item, for the publish date. Optional.
- "date_attr": attribute holding a machine-readable date (e.g. "datetime"). Optional.
- "thumbnail": CSS selector, relative to item, for a thumbnail <img>. Optional.
- "thumbnail_attr": attribute holding the image URL (default "src"). Optional.

Prefer stable, specific selectors (tag + class). Omit optional fields you are
unsure about (do not include them as null). Thumbnail URLs must be http(s).
Output JSON only, no prose."""


@dataclass
class InferResult:
  """Outcome of an inference attempt. `status` distinguishes failure modes so
  the caller (and UI) can tell a zero-match result from invalid/erroring ones."""

  rules: dict[str, str]
  sample_articles: list[Article]
  attempts: int
  status: str  # "ok" | "empty" | "invalid" | "error"


def _is_carousel(el: Any) -> bool:
  """True if the element's class/id marks it as a carousel/slider (not a list)."""
  ident = (" ".join(el.get("class") or []) + " " + (el.get("id") or "")).lower()
  return any(hint in ident for hint in _NOISE_CLASS_HINTS)


def preprocess_html(html: str, max_bytes: int) -> str:
  """Strip noise from HTML and truncate to `max_bytes`, preserving the
  structural attributes (class/id/href/...) selector inference depends on."""
  soup = BeautifulSoup(html, "html.parser")
  for tag in list(soup(list(_NOISE_TAGS))):
    if tag.parent is not None:  # may already be detached as a descendant
      tag.decompose()
  for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
    comment.extract()
  # Remove carousels/sliders. Collect first: decomposing mid-iteration would
  # invalidate descendants find_all() has not yet visited.
  for el in [e for e in soup.find_all(True) if _is_carousel(e)]:
    if el.parent is not None:
      el.decompose()
  for el in soup.find_all(True):
    el.attrs = {k: v for k, v in el.attrs.items() if k in _KEEP_ATTRS}
  text = str(soup)
  encoded = text.encode("utf-8")
  if len(encoded) <= max_bytes:
    return text
  # Article lists sit near the top, so a front-biased byte cut keeps the useful
  # part. Drop any partial trailing character left by the cut.
  return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _coerce_str_dict(value: Any) -> dict[str, str]:
  """Best-effort extraction of string fields from an LLM response, for the
  fallback path where validation failed but we still want to show the attempt."""
  if not isinstance(value, dict):
    return {}
  return {k: v for k, v in value.items() if isinstance(v, str)}


# Path *segments* that mark a listing/index page (category, tag, author, ...)
# rather than an individual article — a weak signal used only to rank candidates.
# Matched per segment (not substring) so "/tag", "/tag/x", "/category" all hit
# while an article slug that merely contains "page" does not.
_INDEX_PATH_SEGMENTS = frozenset({
  "tag", "tags", "category", "categories", "author", "authors",
  "page", "archive", "archives", "search",
})


def _looks_like_article_url(url: str) -> bool:
  """True if the URL path resembles an individual article rather than a
  category/tag/author index. Heuristic, used only for candidate scoring."""
  segments = [seg for seg in urlparse(url).path.lower().split("/") if seg]
  if not segments:  # root or empty path is never an individual article
    return False
  return not any(seg in _INDEX_PATH_SEGMENTS for seg in segments)


def _score_candidate(articles: list[Article]) -> float:
  """Heuristic quality score (higher is better) for an extracted article set.
  Rewards distinct links, distinct titles, and links that look like individual
  article pages. Used only to pick the best candidate across retries — the
  count >= min_articles rule still governs the final ok/empty status."""
  n = len(articles)
  if n == 0:
    return 0.0
  links = [a.url for a in articles]
  titles = [a.title for a in articles if a.title]
  distinct_links = len(set(links)) / n
  distinct_titles = len(set(titles)) / n
  article_like = sum(_looks_like_article_url(u) for u in links) / n
  # Saturating size bonus so a large wrong list cannot win on size alone.
  size_bonus = min(n, 10) / 10
  return distinct_links + distinct_titles + article_like + 0.5 * size_bonus


def _is_clean(articles: list[Article]) -> bool:
  """A candidate worth accepting without spending more retries: every link is
  distinct and looks like an individual article (not a category/tag index)."""
  links = [a.url for a in articles]
  return len(set(links)) == len(links) and all(_looks_like_article_url(u) for u in links)


async def infer_and_validate(
  ollama_client: httpx.AsyncClient,
  model: str,
  html: str,
  *,
  page_url: str,
  source: str,
  max_html_bytes: int,
  max_attempts: int,
  max_items: int = 200,
  min_articles: int = 2,
  num_ctx: int | None = None,
  clean_url_fn: Callable[[str], str] | None = None,
) -> InferResult:
  """Infer extraction rules from HTML and verify them against the real scraper.

  Retries up to `max_attempts`, feeding the prior failure reason back to the
  model. A candidate is accepted early only when it yields >= `min_articles`
  matches AND looks "clean" (distinct links that resemble individual article
  pages, not category/tag/ranking lists). Count-only matches are kept as the
  best-so-far (ranked by `_score_candidate`) and retried, since a list that
  merely has enough rows is often a related/ranking widget. After exhausting
  attempts, the highest-scoring candidate is returned — ok if it meets the
  minimum count, else empty as a manual-editing starting point. Temperature is
  intentionally left high (via the caller) so retries explore different
  structures rather than re-converging on the same wrong one.
  """
  excerpt = preprocess_html(html, max_html_bytes)
  feed_cfg: dict[str, object] = {"name": source, "url": page_url, "max_items": max_items}
  options = {"num_ctx": num_ctx} if num_ctx else None
  base_prompt = f"HTML:\n{excerpt}"
  user_prompt = base_prompt
  last_rules: dict[str, str] = {}
  best: tuple[tuple[bool, float], list[Article], dict[str, str]] | None = None
  status = "error"
  attempts = 0

  for attempt in range(1, max_attempts + 1):
    attempts = attempt
    try:
      raw = await call_ollama(
        ollama_client, model, INFER_SYSTEM_PROMPT, user_prompt, options=options
      )
    except Exception:
      status = "error"
      user_prompt = base_prompt
      continue

    try:
      rules = validate_extraction_rules(raw)
    except ValueError as e:
      last_rules = _coerce_str_dict(raw)
      status = "invalid"
      user_prompt = f"{base_prompt}\n\nYour previous answer was invalid: {e}. Fix it."
      continue

    last_rules = rules
    try:
      articles = parse_web_page(html, rules, feed_cfg, clean_url_fn=clean_url_fn)
    except Exception:
      status = "error"
      user_prompt = f"{base_prompt}\n\nYour selectors failed to apply. Return corrected selectors."
      continue

    if articles:
      # Rank candidates first by whether they meet the count (a usable list),
      # then by quality score, so a count-meeting match is never shadowed by a
      # cleaner-but-too-small one when picking the final best-effort result.
      key = (len(articles) >= min_articles, _score_candidate(articles))
      if best is None or key > best[0]:
        best = (key, articles, rules)
      # Accept immediately only when the set is clean enough; otherwise keep
      # retrying for a better structure (a count-only match may be a related /
      # ranking / tag list rather than the page's main article list).
      if len(articles) >= min_articles and _is_clean(articles):
        return InferResult(
          rules=rules,
          sample_articles=articles[:_SAMPLE_LIMIT],
          attempts=attempt,
          status="ok",
        )
    status = "empty"
    if len(articles) >= min_articles:
      hint = (
        "matched a list, but some links look like category/tag/index pages or "
        "duplicates. Find the page's main list of individual articles"
      )
    elif len(articles) == 1:
      hint = "matched only 1 article. Find the main repeating list of individual articles"
    else:
      hint = "matched zero articles. Re-examine the repeating structure"
    user_prompt = f"{base_prompt}\n\nYour selectors {hint} and return corrected selectors."

  # Exhausted attempts: return the best candidate found (count-meeting first,
  # then highest score). ok if it meets the minimum count (usable, just not
  # "clean"); otherwise empty as a manual-editing starting point.
  if best is not None:
    _key, articles, rules = best
    status = "ok" if len(articles) >= min_articles else "empty"
    return InferResult(
      rules=rules,
      sample_articles=articles[:_SAMPLE_LIMIT],
      attempts=attempts,
      status=status,
    )
  return InferResult(rules=last_rules, sample_articles=[], attempts=attempts, status=status)
