import { describe, it, expect } from "vitest"
import {
  applyUnreadFilter,
  computeFolderFeedOrder,
  nextUnreadFeedId,
  selectArticles,
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
