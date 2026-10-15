"""Repository functions for feeds, articles, and refresh jobs."""

from __future__ import annotations

import math
import random
import re
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Literal

from psycopg import sql
from psycopg.errors import UniqueViolation

from models import Article
from schemas import (
  ArticleRow,
  FeedHealth,
  FeedRow,
  FeedStats,
  FilterRow,
  FolderRow,
  RefreshJob,
)

import db


# -- Adaptive fetch scheduling --

MIN_INTERVAL_MIN = 10
MAX_INTERVAL_MIN = 360  # 6 hours
BUCKETS = (10, 30, 60, 120, 240, 360)
JITTER_RATIO = 0.10
CRON_TICK_MIN = 10
REFRESH_LOCK_KEY = 0xFEEDCAFE  # bigint key for pg_try_advisory_lock

Outcome = Literal["fresh", "miss", "degraded", "failure"]


def step_bucket(cur_min: int, direction: int) -> int:
  """Move one step along BUCKETS; clamp at the endpoints.

  direction > 0 enlarges the interval (miss), direction < 0 shrinks (fresh).
  """
  try:
    idx = BUCKETS.index(cur_min)
  except ValueError:
    # If cur_min isn't a bucket (shouldn't happen due to CHECK), pick the
    # nearest bucket without underflowing.
    idx = min(range(len(BUCKETS)), key=lambda i: abs(BUCKETS[i] - cur_min))
  new_idx = max(0, min(len(BUCKETS) - 1, idx + (1 if direction > 0 else -1)))
  return BUCKETS[new_idx]


def schedule_next_at(now: datetime, interval_min: int) -> datetime:
  """Apply ±JITTER_RATIO jitter, then ceil to the next CRON_TICK_MIN boundary.

  Tick alignment ensures that schedules land on the cron cadence rather than
  between ticks (where they would effectively wait until the next tick).
  """
  factor = 1 + random.uniform(-JITTER_RATIO, JITTER_RATIO)
  raw = now + timedelta(minutes=interval_min * factor)
  tick_s = CRON_TICK_MIN * 60
  snapped = math.ceil(raw.timestamp() / tick_s) * tick_s
  return datetime.fromtimestamp(snapped, tz=timezone.utc)


def record_feed_attempt(
  feed_id: int,
  outcome: Outcome,
  *,
  error: str | None = None,
) -> None:
  """Update a feed's schedule and health columns based on a fetch outcome.

  Centralizes every write to fetch_interval_min / next_fetch_at /
  consecutive_failures / last_error / last_fetched_at so concurrent paths
  cannot step on each other. Selects FOR UPDATE to serialize with toggle
  and other feed-row writes.
  """
  with db.connection() as conn, conn.transaction():
    row = conn.execute(
      "SELECT fetch_interval_min, consecutive_failures, next_fetch_at "
      "FROM feeds WHERE id = %s FOR UPDATE",
      (feed_id,),
    ).fetchone()
    if row is None:
      return
    cur_min = row["fetch_interval_min"]
    fails = row["consecutive_failures"]
    cur_next = row["next_fetch_at"]
    now = datetime.now(tz=timezone.utc)

    if outcome == "failure":
      n = fails + 1
      # Exponential backoff capped at MAX_INTERVAL_MIN: 15, 30, 60, 120, 240, 360.
      backoff = min(MAX_INTERVAL_MIN, 15 * 2 ** max(n - 1, 0))
      # Never pull the next attempt earlier than the current cadence or a
      # previously-scheduled future time.
      raw_next = schedule_next_at(now, max(backoff, cur_min))
      if cur_next is not None and cur_next > raw_next:
        raw_next = cur_next
      conn.execute(
        "UPDATE feeds SET next_fetch_at = %s, consecutive_failures = %s, "
        "last_error = %s, health = 'failing' WHERE id = %s",
        (raw_next, n, error, feed_id),
      )
      return

    if outcome == "degraded":
      # Neutral: keep bucket and fail counter, advance schedule at current
      # cadence. last_error only written when the caller supplies one (e.g.
      # web-norules) so transient cache fallbacks don't wipe diagnostic info.
      next_at = schedule_next_at(now, cur_min)
      if error is not None:
        conn.execute(
          "UPDATE feeds SET next_fetch_at = %s, last_fetched_at = %s, "
          "last_error = %s, health = 'degraded' WHERE id = %s",
          (next_at, now, error, feed_id),
        )
      else:
        conn.execute(
          "UPDATE feeds SET next_fetch_at = %s, last_fetched_at = %s, "
          "health = 'degraded' WHERE id = %s",
          (next_at, now, feed_id),
        )
      return

    # fresh / miss: move the bucket and clear failure state.
    next_min = step_bucket(cur_min, direction=+1 if outcome == "miss" else -1)
    next_at = schedule_next_at(now, next_min)
    conn.execute(
      "UPDATE feeds SET fetch_interval_min = %s, next_fetch_at = %s, "
      "consecutive_failures = 0, last_fetched_at = %s, last_error = NULL, "
      "health = 'ok' WHERE id = %s",
      (next_min, next_at, now, feed_id),
    )


def list_due_feeds(force: bool = False) -> list[FeedRow]:
  """Return enabled feeds that are currently due for fetch.

  force=True returns every enabled feed (used by manual Refresh button).
  force=False returns only feeds whose next_fetch_at is NULL or past.
  """
  with db.connection() as conn:
    if force:
      rows = conn.execute(
        f"SELECT * FROM feeds WHERE enabled {_FEED_ORDER}"
      ).fetchall()
    else:
      rows = conn.execute(
        f"SELECT * FROM feeds "
        f"WHERE enabled AND (next_fetch_at IS NULL OR next_fetch_at <= NOW()) "
        f"{_FEED_ORDER}"
      ).fetchall()
    return [_row_to_feed(r) for r in rows]


@contextmanager
def advisory_lock(key: int = REFRESH_LOCK_KEY) -> Iterator[bool]:
  """Context manager that acquires a session-scoped Postgres advisory lock
  and releases it on exit. Yields True if the lock was acquired, False if
  already held by another session.

  The same pooled connection is held for the duration of the `with` block so
  both acquire and release happen on the same session — session-scoped
  advisory locks would otherwise leak across pool connection reuse.

  The acquiring transaction is committed right away (the session lock outlives
  it) so the connection does not sit "idle in transaction" for the whole block.
  """
  with db.connection() as conn:
    row = conn.execute("SELECT pg_try_advisory_lock(%s) AS got", (key,)).fetchone()
    got = bool(row and row["got"])
    try:
      conn.commit()
      yield got
    finally:
      if got:
        try:
          conn.execute("SELECT pg_advisory_unlock(%s)", (key,))
        except BaseException:
          # Rollback does not release a session lock; ending the session does.
          # The pool replaces the closed connection.
          conn.close()
          raise


# -- Filters --


def list_filters() -> list[FilterRow]:
  with db.connection() as conn:
    rows = conn.execute("SELECT * FROM filters ORDER BY id").fetchall()
    return [FilterRow(**r) for r in rows]


class DuplicateFilterError(ValueError):
  """Raised when a filter equivalent to an existing one is added."""

  def __init__(self, pattern: str, target: str) -> None:
    super().__init__(f'Filter "{pattern}" already exists')
    self.pattern = pattern
    self.target = target


FilterScope = Literal["title", "url", "both"]


def _filter_scope(target: str) -> FilterScope:
  """Normalize a stored target. Anything other than `title` / `url` —
  `both` and the legacy `body` — scans titles and body text."""
  if target == "title":
    return "title"
  if target == "url":
    return "url"
  return "both"


# Article fields each scope matches against. The SQL path and the Python path
# both read this so they cannot drift apart.
_FILTER_COLUMNS: dict[FilterScope, tuple[str, ...]] = {
  "title": ("title", "title_translated"),
  "url": ("url",),
  "both": ("title", "title_translated", "content_snippet", "summary"),
}


# Arbitrary but stable key that serializes concurrent add_filter calls. The
# unique index only catches byte-identical rows, so the equivalence check
# below needs the lock to see every competing insert.
_FILTER_LOCK_KEY = 4_919_431


def filter_key(pattern: str, target: str) -> tuple[bool, str, FilterScope]:
  """Identity of a filter rule, used to reject equivalent duplicates.

  The pattern is taken as stored: whitespace is significant at match time, so
  callers strip user input before it becomes a rule rather than here. Keyword
  patterns are compared lowercased, roughly the folding matching uses (see
  `_contains_match_expr`); regex sources are compared verbatim even though
  they run with IGNORECASE, since lowercasing a source would turn `\\S` into
  `\\s` and merge genuinely different rules. Targets are compared by
  `_filter_scope`, so legacy `body` collapses onto `both`.
  """
  is_regex, body = _parse_filter_pattern(pattern)
  return (
    is_regex,
    body if is_regex else body.lower(),
    _filter_scope(target),
  )


def add_filter(pattern: str, target: str = "title") -> FilterRow:
  """Insert a filter rule. Raises DuplicateFilterError when an equivalent
  rule already exists."""
  pattern = pattern.strip()
  key = filter_key(pattern, target)
  with db.connection() as conn:
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (_FILTER_LOCK_KEY,))
    rows = conn.execute("SELECT pattern, target FROM filters").fetchall()
    if any(filter_key(r["pattern"], r["target"]) == key for r in rows):
      raise DuplicateFilterError(pattern, target)
    try:
      row = conn.execute(
        "INSERT INTO filters (pattern, target) VALUES (%s, %s) RETURNING *",
        (pattern, target),
      ).fetchone()
    except UniqueViolation as exc:
      # Concurrent insert of the very same row; the unique index is the
      # backstop for what the read above could not see.
      raise DuplicateFilterError(pattern, target) from exc
    if row is None:
      raise RuntimeError("add_filter: INSERT returned no row")
    return FilterRow.model_validate(row)


def delete_filter(filter_id: int) -> None:
  with db.connection() as conn:
    conn.execute("DELETE FROM filters WHERE id = %s", (filter_id,))


def _parse_filter_pattern(pattern: str) -> tuple[bool, str]:
  if len(pattern) > 2 and pattern.startswith("/") and pattern.endswith("/"):
    return True, pattern[1:-1]
  return False, pattern


def _like_value(pattern: str) -> str:
  """Wrap `pattern` for a LIKE substring match, escaping LIKE metacharacters.

  `LIKE ANY (array)` takes no ESCAPE clause, so this escapes for the default
  `\\`. Values travel as bind parameters, so `standard_conforming_strings`
  never sees the backslashes.
  """
  esc = pattern.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
  return f"%{esc}%"


@dataclass(frozen=True)
class _RegexRule:
  """A usable regex filter, compiled once per request."""

  pattern: re.Pattern[str]
  columns: tuple[str, ...]


def _split_filters(
  filters: list[FilterRow],
) -> tuple[list[FilterRow], list[_RegexRule]]:
  """Partition filters into (contains, compiled regex rules). Invalid or
  oversized regex patterns are dropped."""
  contains: list[FilterRow] = []
  regexes: list[_RegexRule] = []
  for f in filters:
    is_regex, pat = _parse_filter_pattern(f.pattern)
    if not is_regex:
      contains.append(f)
      continue
    if len(pat) > 200:
      continue
    try:
      compiled = re.compile(pat, re.IGNORECASE)
    except re.error:
      continue
    regexes.append(_RegexRule(compiled, _FILTER_COLUMNS[_filter_scope(f.target)]))
  return contains, regexes


def _db_lower(values: list[str]) -> list[str]:
  """Lowercase `values` with PostgreSQL's `lower()`, preserving order."""
  with db.connection() as conn:
    row = conn.execute(
      "SELECT array("
      "  SELECT lower(v) FROM unnest(%s::text[]) WITH ORDINALITY AS t(v, i)"
      "  ORDER BY i"
      ") AS lowered",
      (values,),
    ).fetchone()
  if row is None:
    raise RuntimeError("_db_lower: query returned no row")
  return list(row["lowered"])


def _contains_match_expr(
  filters: list[FilterRow],
) -> tuple[sql.Composable | None, list[Any]]:
  """Build a Composable expression that is TRUE for any article matching at
  least one of the given contains filters. Returns `(None, [])` when no
  filters are usable, so callers can omit the WHERE fragment entirely.

  One `LIKE ANY` per column instead of one `ILIKE` per rule and column: with
  dozens of rules this lowercases each value once rather than once per rule,
  which is what made the sidebar stats slow. On a multi-byte database `ILIKE`
  is itself `lower(value) LIKE lower(pattern)`, so lowering the patterns with
  the same PostgreSQL `lower()` keeps matches identical; Python's
  `str.lower()` folds some characters differently. The patterns are lowered
  in a separate round trip because a constant array is several times faster
  to match than one computed inside the query.
  """
  patterns_by_column: dict[str, list[str]] = {}
  for f in filters:
    _, pat = _parse_filter_pattern(f.pattern)
    for col in _FILTER_COLUMNS[_filter_scope(f.target)]:
      patterns_by_column.setdefault(col, []).append(pat)
  if not patterns_by_column:
    return None, []
  lowered = iter(_db_lower([p for pats in patterns_by_column.values() for p in pats]))
  clauses: list[sql.Composable] = []
  params: list[Any] = []
  for col, pats in patterns_by_column.items():
    clauses.append(
      sql.SQL("lower(COALESCE({c}, '')) LIKE ANY(%s::text[])").format(c=sql.Identifier(col)),
    )
    params.append([_like_value(next(lowered)) for _ in pats])
  return sql.SQL(" OR ").join(clauses), params


def _matches_regex_rules(row: Mapping[str, Any], rules: list[_RegexRule]) -> bool:
  """True when any rule matches one of its columns in `row`.

  Empty fields are skipped rather than searched, so a pattern like `/^$/`
  cannot hide every article that simply lacks a translated title.
  """
  for rule in rules:
    for col in rule.columns:
      text = row.get(col)
      if not text:
        continue
      if rule.pattern.search(text[:10_000]):
        return True
  return False


# -- Folders --


def list_folders() -> list[FolderRow]:
  with db.connection() as conn:
    rows = conn.execute("SELECT * FROM folders ORDER BY position, id").fetchall()
    return [FolderRow(**r) for r in rows]


def create_folder(name: str) -> FolderRow:
  with db.connection() as conn:
    row = conn.execute(
      """INSERT INTO folders (name, position)
         VALUES (%s, COALESCE((SELECT MAX(position) + 1 FROM folders), 0))
         RETURNING *""",
      (name,),
    ).fetchone()
    if row is None:
      raise RuntimeError("create_folder: INSERT returned no row")
    return FolderRow.model_validate(row)


def rename_folder(folder_id: int, name: str) -> None:
  with db.connection() as conn:
    conn.execute("UPDATE folders SET name = %s WHERE id = %s", (name, folder_id))


def delete_folder(folder_id: int) -> None:
  with db.connection() as conn:
    conn.execute("DELETE FROM folders WHERE id = %s", (folder_id,))


def reorder_folders(folder_ids: list[int]) -> None:
  if not folder_ids:
    return
  with db.connection() as conn, conn.cursor() as cur:
    cur.executemany(
      "UPDATE folders SET position = %s WHERE id = %s",
      [(i, fid) for i, fid in enumerate(folder_ids)],
    )


# -- Feeds --


def move_feed_to_folder(feed_id: int, folder_id: int | None) -> None:
  with db.connection() as conn:
    conn.execute("UPDATE feeds SET folder_id = %s WHERE id = %s", (folder_id, feed_id))


def _row_to_feed(r: dict[str, object]) -> FeedRow:
  return FeedRow.model_validate(r)


_FEED_ORDER = "ORDER BY position, id"


def list_feeds(*, enabled: bool | None = None) -> list[FeedRow]:
  with db.connection() as conn:
    if enabled is None:
      rows = conn.execute(f"SELECT * FROM feeds {_FEED_ORDER}").fetchall()
    else:
      rows = conn.execute(
        f"SELECT * FROM feeds WHERE enabled = %s {_FEED_ORDER}",
        (enabled,),
      ).fetchall()
    return [_row_to_feed(r) for r in rows]


def get_feed_by_id(feed_id: int) -> FeedRow | None:
  with db.connection() as conn:
    row = conn.execute("SELECT * FROM feeds WHERE id = %s", (feed_id,)).fetchone()
    return _row_to_feed(row) if row else None


def add_feed(feed: FeedRow, *, at_top: bool = True) -> FeedRow:
  """Insert a new feed and return the persisted row.

  Positions are global and ordered ascending. By default a new feed gets the
  smallest position (MIN - 1) so an interactively added feed surfaces at the
  front of its section. Bulk callers that must preserve input order (OPML
  import) pass `at_top=False` to append at the end (MAX + 1). Positions may
  drift negative over many top inserts; that is acceptable for INTEGER and
  `reorder_feeds` rebases from the group's MIN.
  """
  # The position expression is a constant chosen by `at_top`, never user input.
  position_expr: str = (
    "COALESCE((SELECT MIN(position) - 1 FROM feeds), 0)"
    if at_top
    else "COALESCE((SELECT MAX(position) + 1 FROM feeds), 0)"
  )
  with db.connection() as conn:
    row = conn.execute(
      f"""INSERT INTO feeds
           (name, url, site_url, translate, summarize, enabled, folder_id, position, extraction_rules)
         VALUES
           (%s, %s, %s, %s, %s, %s, %s,
            {position_expr},
            %s)
         RETURNING *""",
      (
        feed.name,
        feed.url,
        feed.site_url,
        feed.translate,
        feed.summarize,
        feed.enabled,
        feed.folder_id,
        feed.extraction_rules,
      ),
    ).fetchone()
    if row is None:
      raise RuntimeError("add_feed: INSERT returned no row")
    return _row_to_feed(row)


def reorder_feeds(feed_ids: list[int]) -> None:
  """Assign sequential positions to the given feed IDs in the given order.

  Only the listed feeds are re-positioned; positions are re-assigned starting
  at the smallest existing position among them so that unrelated feeds keep
  their relative ordering.
  """
  if not feed_ids:
    return
  with db.connection() as conn, conn.cursor() as cur:
    row = cur.execute(
      "SELECT MIN(position) AS base FROM feeds WHERE id = ANY(%s)",
      (feed_ids,),
    ).fetchone()
    base = row["base"] if row and row["base"] is not None else 0
    cur.executemany(
      "UPDATE feeds SET position = %s WHERE id = %s",
      [(base + i, fid) for i, fid in enumerate(feed_ids)],
    )


def delete_feed(feed_id: int) -> None:
  with db.connection() as conn:
    conn.execute("DELETE FROM feeds WHERE id = %s", (feed_id,))


def delete_all_data() -> None:
  """Delete all feeds, articles, folders, and refresh job history."""
  with db.connection() as conn:
    conn.execute("TRUNCATE articles, feeds, folders, refresh_jobs RESTART IDENTITY CASCADE")


def toggle_feed(feed_id: int) -> None:
  """Flip `enabled`. On the true→false transition, also clear next_fetch_at
  so a later re-enable is treated as immediately due (NULL means "due now").
  The CASE uses the pre-update value of `enabled`, which is the transition
  direction.
  """
  with db.connection() as conn:
    conn.execute(
      "UPDATE feeds "
      "SET enabled = NOT enabled, "
      "    next_fetch_at = CASE WHEN enabled THEN NULL ELSE next_fetch_at END "
      "WHERE id = %s",
      (feed_id,),
    )


def rename_feed(feed_id: int, name: str) -> None:
  with db.connection() as conn:
    conn.execute("UPDATE feeds SET name = %s WHERE id = %s", (name, feed_id))


def update_feed_site_url(feed_id: int, site_url: str | None) -> None:
  with db.connection() as conn:
    conn.execute("UPDATE feeds SET site_url = %s WHERE id = %s", (site_url, feed_id))


def update_feed_extraction_rules(feed_id: int, rules: str | None) -> None:
  with db.connection() as conn:
    conn.execute(
      "UPDATE feeds SET extraction_rules = %s WHERE id = %s",
      (rules, feed_id),
    )


def update_feed_translate(feed_id: int, translate: bool) -> None:
  with db.connection() as conn:
    conn.execute(
      "UPDATE feeds SET translate = %s WHERE id = %s",
      (translate, feed_id),
    )


def get_feed_stats() -> dict[int, FeedStats]:
  """Return per-feed article statistics.

  Filters are applied so counts agree with `list_articles`, which also drops
  filter-matched rows. Without this the sidebar badges would diverge from
  the visible list (e.g. "39 unread" beside "All caught up" when every
  unread article is hidden by an NG-word filter).

  Contains filters are pushed into SQL (see `_contains_match_expr`) so even
  large article tables stay aggregate-only. Regex filters keep Python's `re`
  semantics, which PostgreSQL's `~*` does not share, so when any regex
  filter is active we load just the columns needed to match and count, and
  aggregate in Python.
  """
  filters = list_filters()
  contains_filters, regex_filters = _split_filters(filters)
  match_expr, match_params = _contains_match_expr(contains_filters)

  if not regex_filters:
    where_parts: list[sql.Composable] = [sql.SQL("feed_id IS NOT NULL")]
    if match_expr is not None:
      where_parts.append(sql.SQL("NOT (") + match_expr + sql.SQL(")"))
    where = sql.SQL(" AND ").join(where_parts)
    query = sql.SQL(
      "SELECT feed_id, "
      "       COUNT(*) AS article_count, "
      "       SUM(CASE WHEN read_at IS NULL THEN 1 ELSE 0 END) AS unread_count, "
      "       MAX(published) AS latest_published, "
      "       MIN(published) AS oldest_published "
      "FROM articles WHERE {where} GROUP BY feed_id"
    ).format(where=where)
    with db.connection() as conn:
      rows = conn.execute(query, match_params).fetchall()
    return {
      r["feed_id"]: FeedStats(
        article_count=r["article_count"],
        unread_count=r["unread_count"],
        latest_published=r["latest_published"],
        oldest_published=r["oldest_published"],
      )
      for r in rows
    }

  # Regex fallback: pre-filter with SQL where possible, then apply regex
  # filters in Python on the (already-trimmed) result set.
  where_parts = [sql.SQL("feed_id IS NOT NULL")]
  if match_expr is not None:
    where_parts.append(sql.SQL("NOT (") + match_expr + sql.SQL(")"))
  where = sql.SQL(" AND ").join(where_parts)
  match_columns = sorted({c for rule in regex_filters for c in rule.columns})
  columns = sql.SQL(", ").join(
    sql.Identifier(c) for c in ("feed_id", "read_at", "published", *match_columns)
  )
  query = sql.SQL("SELECT {columns} FROM articles WHERE {where}").format(
    columns=columns, where=where,
  )
  with db.connection() as conn:
    rows = conn.execute(query, match_params).fetchall()
  by_feed: dict[int, list[db.Row]] = {}
  for r in rows:
    if not _matches_regex_rules(r, regex_filters):
      by_feed.setdefault(r["feed_id"], []).append(r)
  result: dict[int, FeedStats] = {}
  for fid, group in by_feed.items():
    pubs = [r["published"] for r in group if r["published"] is not None]
    result[fid] = FeedStats(
      article_count=len(group),
      unread_count=sum(1 for r in group if r["read_at"] is None),
      latest_published=max(pubs) if pubs else None,
      oldest_published=min(pubs) if pubs else None,
    )
  return result


def update_feed_fetch_status(
  feed_id: int,
  *,
  health: FeedHealth,
  error: str | None = None,
) -> None:
  """Record the outcome of a *manual* single-feed fetch.

  Intentionally touches only `last_fetched_at` / `last_error` / `health` — the
  adaptive schedule owns `consecutive_failures` / `fetch_interval_min` /
  `next_fetch_at` via record_feed_attempt, and manual trigger failures must not
  pollute that learning signal (it would inflate backoff on the next scheduled
  run). `health` is written explicitly so a manual failure surfaces as
  "failing" even though the failure counter is left untouched.

  `last_fetched_at` is advanced for any completed attempt (ok or degraded,
  including a served stale cache) but left untouched on a hard failure —
  matching record_feed_attempt's scheduled-path semantics.
  """
  with db.connection() as conn:
    if health == "ok":
      conn.execute(
        "UPDATE feeds SET last_fetched_at = NOW(), last_error = NULL, "
        "health = 'ok' WHERE id = %s",
        (feed_id,),
      )
    elif health == "degraded":
      # Mirror record_feed_attempt's degraded branch: advance last_fetched_at,
      # but only overwrite last_error when a diagnostic is supplied so a bare
      # degraded attempt cannot wipe an existing diagnostic.
      if error is not None:
        conn.execute(
          "UPDATE feeds SET last_fetched_at = NOW(), last_error = %s, "
          "health = 'degraded' WHERE id = %s",
          (error, feed_id),
        )
      else:
        conn.execute(
          "UPDATE feeds SET last_fetched_at = NOW(), health = 'degraded' "
          "WHERE id = %s",
          (feed_id,),
        )
    else:  # failing: a hard failure must not advance last_fetched_at
      conn.execute(
        "UPDATE feeds SET last_error = %s, health = 'failing' WHERE id = %s",
        (error, feed_id),
      )


def toggle_summarize(feed_id: int) -> None:
  with db.connection() as conn:
    conn.execute("UPDATE feeds SET summarize = NOT summarize WHERE id = %s", (feed_id,))


# -- Articles --


# Cap chunk size so we stay well under PostgreSQL's 65535 bound parameters
# limit (10 columns per row * 5000 rows = 50000, with headroom).
_UPSERT_CHUNK = 5000


def _merge_articles(prev: Article, curr: Article) -> Article:
  """Field-wise merge for same-URL duplicates within one upsert batch.
  Mirrors the row-by-row UPSERT loop's effective behavior:

  - The columns that `ON CONFLICT DO UPDATE` actually rewrites
    (content_html/content_snippet/title_translated/summary/content_translated)
    are merged with `later non-empty wins`.
  - The columns that are only set on INSERT (title/source/published, and
    therefore the derived feed_id) keep the first row's values — under the
    old loop, the second row's UPSERT would not touch these.
  """
  return Article(
    url=prev.url,
    # Insert-only columns: first row wins.
    title=prev.title,
    source=prev.source,
    published=prev.published,
    # COALESCE-managed columns: later non-empty wins.
    content_snippet=curr.content_snippet or prev.content_snippet,
    title_translated=curr.title_translated or prev.title_translated,
    summary=curr.summary or prev.summary,
    content_html=curr.content_html or prev.content_html,
    content_translated=curr.content_translated or prev.content_translated,
  )


def upsert_articles(articles: list[Article], feed_id_map: dict[str, int] | None = None) -> int:
  """Insert new articles; update content_* on existing ones if newly available.

  Returns count of newly inserted rows (not updates).

  Uses a single multi-row INSERT per chunk so a refresh of N articles costs
  one round-trip instead of N (the COALESCE-based UPSERT semantics are
  preserved verbatim).

  Same-URL duplicates within `articles` are merged into one row.
  PostgreSQL's `ON CONFLICT DO UPDATE` rejects two rows in one statement
  that target the same conflict key, so we collapse beforehand. The merge
  is field-wise: a non-empty later value wins, otherwise the earlier value
  is kept. This matches the old per-row loop's behavior, where COALESCE in
  the UPSERT preserved any non-empty field across successive same-url
  upserts within the same batch.
  """
  if not articles:
    return 0
  feed_id_map = feed_id_map or {}
  merged: dict[str, Article] = {}
  for a in articles:
    prev = merged.get(a.url)
    merged[a.url] = a if prev is None else _merge_articles(prev, a)
  unique_articles = list(merged.values())
  total = 0
  with db.connection() as conn, conn.cursor() as cur:
    for start in range(0, len(unique_articles), _UPSERT_CHUNK):
      chunk = unique_articles[start:start + _UPSERT_CHUNK]
      values_sql = sql.SQL(", ").join(
        sql.SQL("(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)") for _ in chunk
      )
      query = sql.SQL(
        "INSERT INTO articles "
        "  (url, feed_id, title, title_translated, source, published, "
        "   content_snippet, summary, content_html, content_translated) "
        "VALUES {values} "
        "ON CONFLICT (url) DO UPDATE SET "
        "  content_html       = COALESCE(EXCLUDED.content_html,       articles.content_html), "
        "  content_snippet    = COALESCE(EXCLUDED.content_snippet,    articles.content_snippet), "
        "  title_translated   = COALESCE(EXCLUDED.title_translated,   articles.title_translated), "
        "  summary            = COALESCE(EXCLUDED.summary,            articles.summary), "
        "  content_translated = COALESCE(EXCLUDED.content_translated, articles.content_translated) "
        "RETURNING (xmax = 0) AS inserted"
      ).format(values=values_sql)
      params: list[Any] = []
      for a in chunk:
        params.extend((
          a.url,
          feed_id_map.get(a.source),
          a.title,
          a.title_translated or None,
          a.source,
          a.published,
          a.content_snippet or None,
          a.summary or None,
          a.content_html or None,
          a.content_translated or None,
        ))
      rows = cur.execute(query, params).fetchall()
      total += sum(1 for r in rows if r["inserted"])
  return total


def list_articles(
  *,
  feed_id: int | None = None,
  unread: bool = False,
  since_days: int | None = None,
  read_within_hours: int | None = None,
  limit: int = 10000,
  order: Literal["oldest", "newest"] = "oldest",
) -> list[ArticleRow]:
  """List articles, newest `limit` rows by default.

  With `unread` and `read_within_hours`, rows read within that many hours are
  kept alongside the unread ones, so a client can keep showing what it just
  read; trimming to `limit` then drops read rows before any unread one, so
  read rows can never crowd an old unread row out. The cutoff uses the DB
  clock, the same one that stamps `read_at`.
  """
  filters = list_filters()
  contains_filters, regex_filters = _split_filters(filters)
  match_expr, match_params = _contains_match_expr(contains_filters)

  clauses: list[sql.Composable] = []
  params: list[object] = []
  if feed_id is not None:
    clauses.append(sql.SQL("feed_id = %s"))
    params.append(feed_id)
  prefer_unread = unread and read_within_hours is not None
  if prefer_unread:
    clauses.append(sql.SQL(
      "(read_at IS NULL OR read_at >= NOW() - %s * INTERVAL '1 hour')"
    ))
    params.append(read_within_hours)
  elif unread:
    clauses.append(sql.SQL("read_at IS NULL"))
  if since_days is not None:
    clauses.append(sql.SQL(
      "COALESCE(published, fetched_at) >= NOW() - %s * INTERVAL '1 day'"
    ))
    params.append(since_days)
  if match_expr is not None:
    clauses.append(sql.SQL("NOT (") + match_expr + sql.SQL(")"))
    params.extend(match_params)

  where = sql.SQL("WHERE ") + sql.SQL(" AND ").join(clauses) if clauses else sql.SQL("")
  # Over-fetch only when regex filters need a Python pass after SQL.
  fetch_limit = limit * 3 if regex_filters else limit
  params.append(fetch_limit)
  # SQL returns newest first so LIMIT keeps the most recent `limit` rows
  # even when total >> limit (with `prefer_unread`, unread rows are kept
  # first and the newest read rows fill what is left). By default the response is reversed to
  # oldest-first so the UI can render in ascending publication order without
  # re-sorting; `order="newest"` keeps the SQL's descending order.
  # `fetched_at DESC, url DESC` are tie-breakers: without them, rows that
  # share `COALESCE(published, fetched_at)` (e.g. multiple items from one
  # fetch batch whose feed emits a coarse-granularity pubDate) come back in
  # an implementation-defined order that shuffles between requests.
  # `url` is the PK so the ordering is fully deterministic.
  date_order = sql.SQL(
    "COALESCE(published, fetched_at) DESC NULLS LAST, fetched_at DESC, url DESC"
  )
  if prefer_unread:
    # Pick which rows survive LIMIT unread-first, then hand them back in the
    # usual date order.
    query = sql.SQL(
      "SELECT * FROM ("
      "  SELECT * FROM articles {where} "
      "  ORDER BY (read_at IS NULL) DESC, {date_order} LIMIT %s"
      ") picked ORDER BY {date_order}"
    ).format(where=where, date_order=date_order)
  else:
    query = sql.SQL(
      "SELECT * FROM articles {where} ORDER BY {date_order} LIMIT %s"
    ).format(where=where, date_order=date_order)
  with db.connection() as conn:
    rows = conn.execute(query, params).fetchall()
  if regex_filters:
    rows = [r for r in rows if not _matches_regex_rules(r, regex_filters)]
  articles = [ArticleRow.model_validate(r) for r in rows]
  trimmed = _trim_unread_first(articles, limit) if prefer_unread else articles[:limit]
  return trimmed if order == "newest" else list(reversed(trimmed))


def _trim_unread_first(articles: list[ArticleRow], limit: int) -> list[ArticleRow]:
  """Keep `limit` of the newest-first `articles`, dropping read rows first.

  Mirrors the unread-first LIMIT in SQL for the regex path, which has to
  over-fetch and trim in Python. Date order is preserved.
  """
  if len(articles) <= limit:
    return articles
  unread = [a for a in articles if a.read_at is None][:limit]
  read = [a for a in articles if a.read_at is not None][:limit - len(unread)]
  keep: set[str] = {a.url for a in unread} | {a.url for a in read}
  return [a for a in articles if a.url in keep]


def list_pending_summaries(limit: int = 5) -> list[ArticleRow]:
  """Find articles needing LLM processing.

  Condition must match `fetcher._needs_llm`. The feed-level gate is
  `translate OR summarize`; the article-level done-condition depends on
  (translate, summarize, body length). "Short" means a non-empty body shorter
  than 300 chars; an empty body needs nothing beyond the title translation.
    - translate: title_translated required always; short → content_translated;
      long & summarize → summary; long & not summarize (or empty) → title alone.
    - not translate & summarize: long content (>= 300) without a summary.
  """
  with db.connection() as conn:
    rows = conn.execute(
      """SELECT a.* FROM articles a
         JOIN feeds f ON a.feed_id = f.id
         WHERE (
           (f.translate AND (
             a.title_translated IS NULL
             OR (LENGTH(COALESCE(a.content_snippet, '')) BETWEEN 1 AND 299
                 AND a.content_translated IS NULL)
             OR (LENGTH(COALESCE(a.content_snippet, '')) >= 300
                 AND f.summarize AND a.summary IS NULL)
           ))
           OR
           (NOT f.translate AND f.summarize
             AND LENGTH(COALESCE(a.content_snippet, '')) >= 300
             AND a.summary IS NULL)
         )
         ORDER BY a.published ASC
         LIMIT %s""",
      (limit,),
    ).fetchall()
    return [ArticleRow.model_validate(r) for r in rows]


def update_article_summary(
  url: str,
  title_translated: str | None,
  summary: str | None,
  content_translated: str | None = None,
) -> None:
  with db.connection() as conn:
    conn.execute(
      """UPDATE articles
         SET title_translated   = COALESCE(%s, title_translated),
             summary            = COALESCE(%s, summary),
             content_translated = COALESCE(%s, content_translated)
         WHERE url = %s""",
      (title_translated, summary, content_translated, url),
    )


def mark_read(urls: list[str]) -> None:
  if not urls:
    return
  with db.connection() as conn:
    conn.execute(
      "UPDATE articles SET read_at = NOW() WHERE url = ANY(%s) AND read_at IS NULL",
      (urls,),
    )


def mark_unread(urls: list[str]) -> None:
  if not urls:
    return
  with db.connection() as conn:
    conn.execute("UPDATE articles SET read_at = NULL WHERE url = ANY(%s)", (urls,))


def _scope_clause(
  feed_id: int | None,
  folder_id: int | None,
) -> tuple[list[sql.Composable], list[Any]]:
  """Restrict a bulk read-state update to one feed or one folder.

  `feed_id` wins when both are given — a single feed is always narrower than
  the folder holding it. Without either, the update stays global.
  """
  if feed_id is not None:
    return [sql.SQL("feed_id = %s")], [feed_id]
  if folder_id is not None:
    return [sql.SQL("feed_id IN (SELECT id FROM feeds WHERE folder_id = %s)")], [folder_id]
  return [], []


def mark_all_unread(feed_id: int | None = None, folder_id: int | None = None) -> int:
  scope_clauses, params = _scope_clause(feed_id, folder_id)
  clauses: list[sql.Composable] = [sql.SQL("read_at IS NOT NULL"), *scope_clauses]
  query = sql.SQL("UPDATE articles SET read_at = NULL WHERE {}").format(
    sql.SQL(" AND ").join(clauses),
  )
  with db.connection() as conn:
    cur = conn.execute(query, params)
    return cur.rowcount


def mark_all_read(
  feed_id: int | None = None,
  older_than_hours: int | None = None,
  folder_id: int | None = None,
) -> int:
  # Age is measured against COALESCE(published, fetched_at) so feeds without a
  # `published` value still age out based on when we first saw them.
  scope_clauses, params = _scope_clause(feed_id, folder_id)
  clauses: list[sql.Composable] = [sql.SQL("read_at IS NULL"), *scope_clauses]
  if older_than_hours is not None and older_than_hours > 0:
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=older_than_hours)
    clauses.append(sql.SQL("COALESCE(published, fetched_at) < %s"))
    params.append(cutoff)
  query = sql.SQL("UPDATE articles SET read_at = NOW() WHERE {}").format(
    sql.SQL(" AND ").join(clauses),
  )
  with db.connection() as conn:
    cur = conn.execute(query, params)
    return cur.rowcount


def prune_articles(max_age_days: int) -> int:
  # 0 (or negative) disables pruning — articles are retained indefinitely.
  if max_age_days <= 0:
    return 0
  cutoff = datetime.now(tz=timezone.utc) - timedelta(days=max_age_days)
  with db.connection() as conn:
    cur = conn.execute("DELETE FROM articles WHERE fetched_at < %s", (cutoff,))
    return cur.rowcount


# -- Refresh jobs --


def create_refresh_job(source: str) -> int:
  with db.connection() as conn:
    row = conn.execute(
      "INSERT INTO refresh_jobs (source, status) VALUES (%s, 'running') RETURNING id",
      (source,),
    ).fetchone()
    return row["id"] if row else 0


def finish_refresh_job(
  job_id: int,
  status: str,
  new_count: int,
  error: str | None,
) -> None:
  with db.connection() as conn:
    conn.execute(
      """UPDATE refresh_jobs
         SET finished_at = NOW(), status = %s, new_count = %s, error = %s
         WHERE id = %s""",
      (status, new_count, error, job_id),
    )


def get_latest_refresh_job() -> RefreshJob | None:
  with db.connection() as conn:
    row = conn.execute(
      "SELECT * FROM refresh_jobs ORDER BY id DESC LIMIT 1",
    ).fetchone()
    return RefreshJob.model_validate(row) if row else None
