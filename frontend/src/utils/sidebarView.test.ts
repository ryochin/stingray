import { describe, it, expect } from "vitest"
import { folderUnreadCount, groupFeedsByFolder, totalUnread } from "./sidebarView"
import type { Feed } from "../api/client"

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


describe("groupFeedsByFolder", () => {
  it("buckets by folder_id with null as its own key", () => {
    const feeds = [
      feed(1, { folder_id: 10 }),
      feed(2, { folder_id: 10 }),
      feed(3, { folder_id: 20 }),
      feed(4, { folder_id: null }),
    ]
    const grouped = groupFeedsByFolder(feeds)
    expect(grouped.get(10)!.map((f) => f.id)).toEqual([1, 2])
    expect(grouped.get(20)!.map((f) => f.id)).toEqual([3])
    expect(grouped.get(null)!.map((f) => f.id)).toEqual([4])
  })

  it("excludes disabled feeds", () => {
    const feeds = [
      feed(1, { folder_id: 10, enabled: true }),
      feed(2, { folder_id: 10, enabled: false }),
    ]
    const grouped = groupFeedsByFolder(feeds)
    expect(grouped.get(10)!.map((f) => f.id)).toEqual([1])
  })

  it("handles undefined feeds list gracefully", () => {
    expect(groupFeedsByFolder(undefined).size).toBe(0)
  })

  it("preserves input order within a folder", () => {
    const feeds = [
      feed(3, { folder_id: 1 }),
      feed(1, { folder_id: 1 }),
      feed(2, { folder_id: 1 }),
    ]
    const grouped = groupFeedsByFolder(feeds)
    // Grouping does not sort — that's the caller's responsibility.
    expect(grouped.get(1)!.map((f) => f.id)).toEqual([3, 1, 2])
  })
})


describe("folderUnreadCount", () => {
  it("sums unread across all feeds in a folder", () => {
    const grouped = new Map([
      [1, [feed(10, { folder_id: 1 }), feed(20, { folder_id: 1 })]],
    ])
    const unread = new Map([[10, 3], [20, 5], [99, 7]])
    expect(folderUnreadCount(grouped, unread, 1)).toBe(8)
  })

  it("returns 0 for unknown folder", () => {
    expect(folderUnreadCount(new Map(), new Map(), 42)).toBe(0)
  })

  it("treats missing unread entries as 0", () => {
    const grouped = new Map([[1, [feed(10), feed(20)]]])
    expect(folderUnreadCount(grouped, new Map([[10, 4]]), 1)).toBe(4)
  })
})


describe("totalUnread", () => {
  it("sums all values in the map", () => {
    expect(totalUnread(new Map([[1, 2], [2, 3], [3, 0]]))).toBe(5)
  })

  it("returns 0 for an empty map", () => {
    expect(totalUnread(new Map())).toBe(0)
  })
})
