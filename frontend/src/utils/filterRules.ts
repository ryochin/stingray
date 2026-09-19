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

/**
 * Identity of a filter rule. The pattern is taken as stored — callers trim
 * user input first — keywords match case-insensitively, and `body` / `both`
 * scan the same columns. Regex sources keep their case: lowercasing one would
 * turn `\S` into `\s` and merge rules that are genuinely different.
 */
export const filterKey = (pattern: string, target: string): string => {
  const regex: boolean = isRegexPattern(pattern)
  const body: string = regex ? pattern.slice(1, -1) : pattern.toLowerCase()
  const scope: string = target === "title" ? "title" : "both"
  return `${regex ? "re" : "kw"}\u0000${body}\u0000${scope}`
}
