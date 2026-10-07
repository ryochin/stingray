/**
 * Filter rule helpers shared by the Filters screen.
 *
 * The rules mirror `_parse_filter_pattern` / `filter_key` in backend/repo.py.
 * The backend stays the authority — it answers 409 on a duplicate — but the
 * same key is computed here so the form can warn before the round trip.
 */

/** A pattern wrapped in slashes is a regular expression, not a keyword. */
export const isRegexPattern = (pattern: string): boolean =>
  pattern.length > 2 && pattern.startsWith("/") && pattern.endsWith("/")

/** Targets a new rule can be created with. */
export type FilterTarget = "title" | "both" | "url"

/**
 * Normalize a stored target, mirroring `_filter_scope` in backend/repo.py:
 * anything other than `title` / `url` — `both` and the legacy `body` — scans
 * titles and body text.
 */
const filterScope = (target: string): FilterTarget =>
  target === "title" || target === "url" ? target : "both"

/** Display names, in the order the target picker lists them. */
export const FILTER_TARGET_LABELS: Record<FilterTarget, string> = {
  title: "Title",
  both: "Title & Body",
  url: "URL",
}

/** Display name of a stored target; legacy `body` reads as `both`. */
export const filterTargetLabel = (target: string): string =>
  FILTER_TARGET_LABELS[filterScope(target)]

/**
 * Identity of a filter rule. The pattern is taken as stored — callers trim
 * user input first — keywords match case-insensitively, and targets compare
 * by scope. Regex sources keep their case: lowercasing one would turn `\S`
 * into `\s` and merge rules that are genuinely different.
 */
export const filterKey = (pattern: string, target: string): string => {
  const regex: boolean = isRegexPattern(pattern)
  const body: string = regex ? pattern.slice(1, -1) : pattern.toLowerCase()
  const scope: FilterTarget = filterScope(target)
  return `${regex ? "re" : "kw"}\u0000${body}\u0000${scope}`
}
