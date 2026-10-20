/**
 * Shared test helpers. Not a `*.test.ts` file, so vitest's `include`
 * (`src/**\/*.test.{ts,tsx}`) does not pick it up as a suite.
 *
 * `vi.mock` factories are hoisted above imports and cannot live here — only
 * plain data builders and stubs belong in this file.
 */

import type { Article, Feed } from "./api/client"

/** happy-dom implements neither observer, and several components construct
 *  them unconditionally. Install with `vi.stubGlobal`. */
export class NoopObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
  takeRecords(): [] {
    return []
  }
}

export function makeFeed(overrides: Partial<Feed> = {}): Feed {
  return {
    id: 1,
    name: "Test Feed",
    url: "https://x.test/feed",
    site_url: null,
    translate: false,
    summarize: false,
    enabled: true,
    folder_id: null,
    position: 0,
    last_fetched_at: null,
    consecutive_failures: 0,
    last_error: null,
    health: "ok",
    // null keeps the card out of the lazy ExtractionRulesEditor (Suspense).
    extraction_rules: null,
    created_at: "2026-07-05T00:00:00Z",
    ...overrides,
  }
}

export function makeArticle(
  url: string,
  overrides: Partial<Article> = {},
): Article {
  return {
    url,
    feed_id: 1,
    title: url,
    title_translated: null,
    source: "s",
    published: null,
    content_snippet: null,
    summary: null,
    content_html: null,
    content_translated: null,
    read_at: null,
    ...overrides,
  }
}
