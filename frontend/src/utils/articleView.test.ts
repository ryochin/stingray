import { describe, it, expect } from "vitest"
import {
  applyTimeFilter,
  applyUnreadFilter,
  computeFolderFeedOrder,
  nextUnreadFeedId,
  parseTimeRangeId,
  selectArticles,
  timeRangeDays,
} from "./articleView"
import type { Article, Feed, Selection } from "../api/client"

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
    const feeds = [
      feed(10, { folder_id: 1, position: 2 }),
      feed(20, { folder_id: 1, position: 0 }),
      feed(30, { folder_id: 1, position: 0 }),
    ]
    const order = computeFolderFeedOrder({ type: "folder", id: 1 }, feeds)
    expect(order).not.toBeNull()
    // Expected sidebar order: position asc (0,0,2), id asc on tie → 20, 30, 10.
    expect(order!.get(20)).toBe(0)
    expect(order!.get(30)).toBe(1)
    expect(order!.get(10)).toBe(2)
  })

  it("excludes feeds from other folders and disabled feeds", () => {
    const feeds = [
      feed(1, { folder_id: 1, enabled: true }),
      feed(2, { folder_id: 2, enabled: true }),
      feed(3, { folder_id: 1, enabled: false }),
    ]
    const order = computeFolderFeedOrder({ type: "folder", id: 1 }, feeds)
    expect(Array.from(order!.keys())).toEqual([1])
  })
})


describe("selectArticles", () => {
  const articles = [
    article({ url: "a1", feed_id: 1 }),
    article({ url: "a2", feed_id: 2 }),
    article({ url: "a3", feed_id: 3 }),
  ]

  it("filters by feed_id for feed selection", () => {
    const out = selectArticles(articles, { type: "feed", id: 2 }, null)
    expect(out.map((a) => a.url)).toEqual(["a2"])
  })

  it("returns all articles for 'all' selection", () => {
    const out = selectArticles(articles, { type: "all" }, null)
    expect(out.length).toBe(3)
  })

  it("folder selection sorts by feed index, preserving published order within a feed (stable sort)", () => {
    // Feed index: 1→0, 2→1
    const order = new Map([[1, 0], [2, 1]])
    const list = [
      article({ url: "f2-early", feed_id: 2 }),
      article({ url: "f1-a", feed_id: 1 }),
      article({ url: "f2-late", feed_id: 2 }),
      article({ url: "f1-b", feed_id: 1 }),
      article({ url: "f3-other", feed_id: 3 }),  // excluded
    ]
    const out = selectArticles(list, { type: "folder", id: 1 }, order)
    // f1 group first (preserving input order: f1-a, f1-b), then f2 group (f2-early, f2-late).
    expect(out.map((a) => a.url)).toEqual(["f1-a", "f1-b", "f2-early", "f2-late"])
  })

  it("folder selection drops articles whose feed is not in the folder", () => {
    const order = new Map([[1, 0]])
    const out = selectArticles(articles, { type: "folder", id: 99 }, order)
    expect(out.map((a) => a.url)).toEqual(["a1"])
  })

  it("folder with null folderFeedOrder returns all (defensive)", () => {
    const out = selectArticles(articles, { type: "folder", id: 1 }, null)
    expect(out.length).toBe(3)
  })
})


describe("applyUnreadFilter", () => {
  const list = [
    article({ url: "unread", read_at: null }),
    article({ url: "read", read_at: "2024-01-01T00:00:00Z" }),
    article({ url: "just-read", read_at: "2024-01-01T00:00:00Z" }),
  ]

  it("returns all when showUnreadOnly is false", () => {
    expect(applyUnreadFilter(list, false, new Set()).length).toBe(3)
  })

  it("drops already-read articles not in session set", () => {
    const out = applyUnreadFilter(list, true, new Set())
    expect(out.map((a) => a.url)).toEqual(["unread"])
  })

  it("keeps session-read URLs so users still see what they just dismissed", () => {
    const out = applyUnreadFilter(list, true, new Set(["just-read"]))
    expect(out.map((a) => a.url).sort()).toEqual(["just-read", "unread"])
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


describe("applyTimeFilter", () => {
  const now = new Date("2026-01-20T12:00:00Z")
  const list = [
    article({ url: "today",      published: "2026-01-20T09:00:00Z" }),
    article({ url: "yesterday",  published: "2026-01-19T12:00:00Z" }),
    article({ url: "four-days",  published: "2026-01-16T12:00:00Z" }),
    article({ url: "ten-days",   published: "2026-01-10T12:00:00Z" }),
    article({ url: "ancient",    published: "2020-01-01T00:00:00Z" }),
    article({ url: "no-date",    published: null }),
    article({ url: "garbled",    published: "not-a-date" }),
  ]

  it("returns everything untouched for 'all'", () => {
    const out = applyTimeFilter(list, "all", now)
    expect(out).toEqual(list)
  })

  it("keeps only articles within the given window", () => {
    const out = applyTimeFilter(list, "3d", now)
    // 3-day threshold = 2026-01-17T12:00Z. today & yesterday pass; four-days
    // is outside. no-date/garbled are kept (can't be positively excluded).
    expect(out.map((a) => a.url).sort()).toEqual(
      ["garbled", "no-date", "today", "yesterday"].sort(),
    )
  })

  it("keeps articles whose published exactly matches the threshold (>= compare)", () => {
    // Boundary article published exactly 7 days before `now`.
    const boundary = article({
      url: "boundary",
      published: new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000).toISOString(),
    })
    const out = applyTimeFilter([boundary], "7d", now)
    expect(out.map((a) => a.url)).toEqual(["boundary"])
  })

  it("keeps articles with null published", () => {
    const out = applyTimeFilter([article({ url: "no-date", published: null })], "1d", now)
    expect(out.map((a) => a.url)).toEqual(["no-date"])
  })

  it("keeps articles with unparseable published string", () => {
    const out = applyTimeFilter(
      [article({ url: "garbled", published: "not-a-date" })],
      "1d",
      now,
    )
    expect(out.map((a) => a.url)).toEqual(["garbled"])
  })

  it("30d id really corresponds to 30 days", () => {
    const thirtyOne = article({
      url: "31d",
      published: new Date(now.getTime() - 31 * 24 * 60 * 60 * 1000).toISOString(),
    })
    const twentyNine = article({
      url: "29d",
      published: new Date(now.getTime() - 29 * 24 * 60 * 60 * 1000).toISOString(),
    })
    const out = applyTimeFilter([thirtyOne, twentyNine], "30d", now)
    expect(out.map((a) => a.url)).toEqual(["29d"])
  })
})


describe("nextUnreadFeedId", () => {
  it("returns the next feed id with unread > 0", () => {
    const ordered = [1, 2, 3, 4]
    const unread = new Map([[1, 0], [2, 0], [3, 5], [4, 2]])
    expect(nextUnreadFeedId(ordered, 1, unread)).toBe(3)
  })

  it("skips feeds with zero unread", () => {
    const ordered = [1, 2, 3, 4]
    const unread = new Map([[1, 3], [2, 0], [3, 0], [4, 1]])
    expect(nextUnreadFeedId(ordered, 1, unread)).toBe(4)
  })

  it("returns null when no subsequent feed has unread", () => {
    const ordered = [1, 2, 3]
    const unread = new Map([[1, 0], [2, 0], [3, 0]])
    expect(nextUnreadFeedId(ordered, 1, unread)).toBeNull()
  })

  it("returns null when current id is last in order", () => {
    expect(nextUnreadFeedId([1, 2, 3], 3, new Map([[1, 5]]))).toBeNull()
  })

  it("returns null when current id is not in order", () => {
    expect(nextUnreadFeedId([1, 2, 3], 999, new Map([[1, 5]]))).toBeNull()
  })

  it("treats missing map entries as zero unread", () => {
    // Feed 2 is absent from unread map → treated as 0 and skipped.
    const ordered = [1, 2, 3]
    const unread = new Map([[3, 1]])
    expect(nextUnreadFeedId(ordered, 1, unread)).toBe(3)
  })
})
