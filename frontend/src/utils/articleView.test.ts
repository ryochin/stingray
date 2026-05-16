import { describe, expect, it } from "vitest"
import type { Article, Feed, FeedStats, Selection } from "../api/client"
import {
  applyUnreadFilter,
  computeFolderFeedOrder,
  deriveUnreadCounts,
  nextUnreadFeedId,
  parseTimeRangeId,
  selectArticles,
  tallySessionReadByFeed,
  timeRangeDays,
} from "./articleView"

function feed(id: number, overrides: Partial<Feed> = {}): Feed {
  return {
    id,
    name: `F${id}`,
    url: `https://f${id}.example.com/`,
    site_url: null,
    translate: false,
    summarize: true,
    enabled: true,
    folder_id: null,
    position: 0,
    last_fetched_at: null,
    consecutive_failures: 0,
    last_error: null,
    extraction_rules: null,
    created_at: "2024-01-01T00:00:00Z",
    ...overrides,
  }
}

function article(overrides: Partial<Article> & { url: string }): Article {
  return {
    feed_id: 1,
    title: "t",
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

describe("computeFolderFeedOrder", () => {
  it("returns null for non-folder selection", () => {
    const sel: Selection = { type: "all" }
    expect(computeFolderFeedOrder(sel, [feed(1)])).toBeNull()
  })

  it("returns null when feeds are undefined", () => {
    const sel: Selection = { type: "folder", id: 1 }
    expect(computeFolderFeedOrder(sel, undefined)).toBeNull()
  })

  it("orders feeds by position then id and indexes them", () => {
    const feeds: Feed[] = [
      feed(10, { folder_id: 1, position: 2 }),
      feed(20, { folder_id: 1, position: 0 }),
      feed(30, { folder_id: 1, position: 0 }),
    ]
    const order: Map<number, number> | null = computeFolderFeedOrder(
      { type: "folder", id: 1 },
      feeds,
    )
    expect(order).not.toBeNull()
    // Expected sidebar order: position asc (0,0,2), id asc on tie → 20, 30, 10.
    expect(order?.get(20)).toBe(0)
    expect(order?.get(30)).toBe(1)
    expect(order?.get(10)).toBe(2)
  })

  it("excludes feeds from other folders and disabled feeds", () => {
    const feeds: Feed[] = [
      feed(1, { folder_id: 1, enabled: true }),
      feed(2, { folder_id: 2, enabled: true }),
      feed(3, { folder_id: 1, enabled: false }),
    ]
    const order: Map<number, number> | null = computeFolderFeedOrder(
      { type: "folder", id: 1 },
      feeds,
    )
    expect(Array.from((order as Map<number, number>).keys())).toEqual([1])
  })
})

describe("selectArticles", () => {
  const articles: Article[] = [
    article({ url: "a1", feed_id: 1 }),
    article({ url: "a2", feed_id: 2 }),
    article({ url: "a3", feed_id: 3 }),
  ]

  it("filters by feed_id for feed selection", () => {
    const out: Article[] = selectArticles(
      articles,
      { type: "feed", id: 2 },
      null,
    )
    expect(out.map((a: Article): string => a.url)).toEqual(["a2"])
  })

  it("returns all articles for 'all' selection", () => {
    const out: Article[] = selectArticles(articles, { type: "all" }, null)
    expect(out.length).toBe(3)
  })

  it("folder selection sorts by feed index, preserving published order within a feed (stable sort)", () => {
    // Feed index: 1→0, 2→1
    const order: Map<number, number> = new Map([
      [1, 0],
      [2, 1],
    ])
    const list: Article[] = [
      article({ url: "f2-early", feed_id: 2 }),
      article({ url: "f1-a", feed_id: 1 }),
      article({ url: "f2-late", feed_id: 2 }),
      article({ url: "f1-b", feed_id: 1 }),
      article({ url: "f3-other", feed_id: 3 }), // excluded
    ]
    const out: Article[] = selectArticles(
      list,
      { type: "folder", id: 1 },
      order,
    )
    // f1 group first (preserving input order: f1-a, f1-b), then f2 group (f2-early, f2-late).
    expect(out.map((a: Article): string => a.url)).toEqual([
      "f1-a",
      "f1-b",
      "f2-early",
      "f2-late",
    ])
  })

  it("folder selection drops articles whose feed is not in the folder", () => {
    const order: Map<number, number> = new Map([[1, 0]])
    const out: Article[] = selectArticles(
      articles,
      { type: "folder", id: 99 },
      order,
    )
    expect(out.map((a: Article): string => a.url)).toEqual(["a1"])
  })

  it("folder with null folderFeedOrder returns all (defensive)", () => {
    const out: Article[] = selectArticles(
      articles,
      { type: "folder", id: 1 },
      null,
    )
    expect(out.length).toBe(3)
  })
})

describe("applyUnreadFilter", () => {
  const list: Article[] = [
    article({ url: "unread", read_at: null }),
    article({ url: "read", read_at: "2024-01-01T00:00:00Z" }),
    article({ url: "just-read", read_at: "2024-01-01T00:00:00Z" }),
  ]

  it("returns all when showUnreadOnly is false", () => {
    expect(applyUnreadFilter(list, false, new Set()).length).toBe(3)
  })

  it("drops already-read articles not in session set", () => {
    const out: Article[] = applyUnreadFilter(list, true, new Set())
    expect(out.map((a: Article): string => a.url)).toEqual(["unread"])
  })

  it("keeps session-read URLs so users still see what they just dismissed", () => {
    const out: Article[] = applyUnreadFilter(list, true, new Set(["just-read"]))
    expect(out.map((a: Article): string => a.url).sort()).toEqual([
      "just-read",
      "unread",
    ])
  })
})

describe("parseTimeRangeId", () => {
  it("returns the id when input matches a known range", () => {
    expect(parseTimeRangeId("1d")).toBe("1d")
    expect(parseTimeRangeId("7d")).toBe("7d")
    expect(parseTimeRangeId("all")).toBe("all")
  })

  it("falls back to 'all' for unknown or malformed input", () => {
    expect(parseTimeRangeId("")).toBe("all")
    expect(parseTimeRangeId("2d")).toBe("all")
    expect(parseTimeRangeId("foo")).toBe("all")
    expect(parseTimeRangeId(null)).toBe("all")
    expect(parseTimeRangeId(undefined)).toBe("all")
    expect(parseTimeRangeId(7)).toBe("all")
    expect(parseTimeRangeId({ id: "7d" })).toBe("all")
  })
})

describe("timeRangeDays", () => {
  it("maps known ids to the expected day count", () => {
    expect(timeRangeDays("1d")).toBe(1)
    expect(timeRangeDays("3d")).toBe(3)
    expect(timeRangeDays("7d")).toBe(7)
    expect(timeRangeDays("14d")).toBe(14)
    expect(timeRangeDays("30d")).toBe(30)
  })

  it("returns null for the 'all' sentinel", () => {
    expect(timeRangeDays("all")).toBeNull()
  })
})

describe("tallySessionReadByFeed", () => {
  it("counts session-read URLs per feed via the article→feed map", () => {
    const articles: Article[] = [
      article({ url: "a1", feed_id: 1 }),
      article({ url: "a2", feed_id: 1 }),
      article({ url: "b1", feed_id: 2 }),
    ]
    const tally: Map<number, number> = tallySessionReadByFeed(
      articles,
      new Set(["a1", "a2", "b1"]),
    )
    expect(tally.get(1)).toBe(2)
    expect(tally.get(2)).toBe(1)
  })

  it("ignores URLs not present in the article list", () => {
    const articles: Article[] = [article({ url: "a1", feed_id: 1 })]
    const tally: Map<number, number> = tallySessionReadByFeed(
      articles,
      new Set(["a1", "ghost"]),
    )
    expect(tally.get(1)).toBe(1)
    expect(tally.size).toBe(1)
  })

  it("ignores articles with null feed_id", () => {
    const articles: Article[] = [article({ url: "a1", feed_id: null })]
    const tally: Map<number, number> = tallySessionReadByFeed(
      articles,
      new Set(["a1"]),
    )
    expect(tally.size).toBe(0)
  })

  it("ignores articles whose read_at is already populated", () => {
    // Stats has already absorbed this read after the post-markRead refetch;
    // counting it locally too would double-decrement the badge.
    const articles: Article[] = [
      article({ url: "fresh", feed_id: 1, read_at: null }),
      article({ url: "synced", feed_id: 1, read_at: "2026-04-30T00:00:00Z" }),
    ]
    const tally: Map<number, number> = tallySessionReadByFeed(
      articles,
      new Set(["fresh", "synced"]),
    )
    expect(tally.get(1)).toBe(1)
  })
})

function stats(unread: number): FeedStats {
  return {
    article_count: unread + 5,
    unread_count: unread,
    latest_published: null,
    oldest_published: null,
  }
}

describe("deriveUnreadCounts", () => {
  it("returns empty map when stats or enabledFeedIds is missing", () => {
    expect(deriveUnreadCounts(undefined, new Set([1]), new Map()).size).toBe(0)
    expect(deriveUnreadCounts({ "1": stats(3) }, null, new Map()).size).toBe(0)
  })

  it("excludes disabled feeds even when stats lists them", () => {
    const out: Map<number, number> = deriveUnreadCounts(
      { "1": stats(3), "2": stats(5) },
      new Set([1]),
      new Map(),
    )
    expect(out.get(1)).toBe(3)
    expect(out.has(2)).toBe(false)
  })

  it("subtracts session-local reads", () => {
    const out: Map<number, number> = deriveUnreadCounts(
      { "1": stats(10) },
      new Set([1]),
      new Map([[1, 4]]),
    )
    expect(out.get(1)).toBe(6)
  })

  it("clamps at zero when local reads outpace stats", () => {
    const out: Map<number, number> = deriveUnreadCounts(
      { "1": stats(2) },
      new Set([1]),
      new Map([[1, 5]]),
    )
    expect(out.has(1)).toBe(false) // count clamped to 0, then dropped
  })

  it("omits feeds whose effective count is zero", () => {
    const out: Map<number, number> = deriveUnreadCounts(
      { "1": stats(0), "2": stats(3) },
      new Set([1, 2]),
      new Map(),
    )
    expect(out.has(1)).toBe(false)
    expect(out.get(2)).toBe(3)
  })
})

describe("nextUnreadFeedId", () => {
  it("returns the next feed id with unread > 0", () => {
    const ordered: number[] = [1, 2, 3, 4]
    const unread: Map<number, number> = new Map([
      [1, 0],
      [2, 0],
      [3, 5],
      [4, 2],
    ])
    expect(nextUnreadFeedId(ordered, 1, unread)).toBe(3)
  })

  it("skips feeds with zero unread", () => {
    const ordered: number[] = [1, 2, 3, 4]
    const unread: Map<number, number> = new Map([
      [1, 3],
      [2, 0],
      [3, 0],
      [4, 1],
    ])
    expect(nextUnreadFeedId(ordered, 1, unread)).toBe(4)
  })

  it("returns null when no other feed has unread", () => {
    const ordered: number[] = [1, 2, 3]
    const unread: Map<number, number> = new Map([
      [1, 0],
      [2, 0],
      [3, 0],
    ])
    expect(nextUnreadFeedId(ordered, 1, unread)).toBeNull()
  })

  it("wraps to head when current is last and head has unread", () => {
    expect(nextUnreadFeedId([1, 2, 3], 3, new Map([[1, 5]]))).toBe(1)
  })

  it("returns null when current id is not in order", () => {
    expect(nextUnreadFeedId([1, 2, 3], 999, new Map([[1, 5]]))).toBeNull()
  })

  it("treats missing map entries as zero unread", () => {
    // Feed 2 is absent from unread map → treated as 0 and skipped.
    const ordered: number[] = [1, 2, 3]
    const unread: Map<number, number> = new Map([[3, 1]])
    expect(nextUnreadFeedId(ordered, 1, unread)).toBe(3)
  })

  it("wraps past tail and skips zero-unread feeds at head", () => {
    const ordered: number[] = [1, 2, 3, 4, 5]
    const unread: Map<number, number> = new Map([
      [1, 0],
      [2, 3],
      [5, 0],
    ])
    // From current=4: 5 has no unread, wrap to 1 (skip), then hit 2.
    expect(nextUnreadFeedId(ordered, 4, unread)).toBe(2)
  })

  it("does not return current itself when only current has unread", () => {
    expect(nextUnreadFeedId([1, 2, 3], 2, new Map([[2, 7]]))).toBeNull()
  })

  it("returns null when ordered has a single feed (n=1 boundary)", () => {
    // step < n means the loop body never runs, so current can never be
    // returned even though it has unread items.
    expect(nextUnreadFeedId([2], 2, new Map([[2, 7]]))).toBeNull()
  })
})
