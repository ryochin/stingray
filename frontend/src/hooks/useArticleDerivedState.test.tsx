import { act, renderHook } from "@testing-library/react"
import { useRef } from "react"
import { describe, expect, it } from "vitest"
import type { Article, Feed, FeedStats, Folder, Selection } from "../api/client"
import { useArticleDerivedState } from "./useArticleDerivedState"

function feed(id: number, overrides: Partial<Feed> = {}): Feed {
  return {
    id,
    name: `F${id}`,
    url: `https://f${id}.example.com/`,
    site_url: null,
    translate: false,
    summarize: false,
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

function folder(id: number, position: number): Folder {
  return { id, name: `Folder${id}`, position }
}

function article(
  url: string,
  feedId: number | null,
  overrides: Partial<Article> = {},
): Article {
  return {
    url,
    feed_id: feedId,
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

interface HookInput {
  feeds: Feed[] | undefined
  folders: Folder[] | undefined
  feedStats: Record<string, FeedStats> | undefined
  allArticles: Article[] | undefined
  selection: Selection
  showUnreadOnly: boolean
  sessionReadSeed?: string[]
  localReadCount?: number
}

// Wrap the hook so each render uses a stable `sessionReadUrls` ref seeded
// from the test input. The hook treats the ref as opaque storage and
// invalidates memos via `localReadCount`, matching the production wiring
// from `usePendingReads`.
function useHarness(input: HookInput) {
  const sessionReadUrls = useRef<Set<string>>(
    new Set(input.sessionReadSeed ?? []),
  )
  return {
    sessionReadUrls,
    result: useArticleDerivedState({
      feeds: input.feeds,
      folders: input.folders,
      feedStats: input.feedStats,
      allArticles: input.allArticles,
      selection: input.selection,
      showUnreadOnly: input.showUnreadOnly,
      sessionReadUrls,
      localReadCount: input.localReadCount ?? 0,
    }),
  }
}

describe("useArticleDerivedState — feedMap / orderedFeedIds / enabledFeedIds", (): void => {
  it("indexes feeds by id (feedMap) and skips missing feed inputs", (): void => {
    const { result } = renderHook(
      (): ReturnType<typeof useHarness> =>
        useHarness({
          feeds: [feed(10), feed(20, { name: "Twenty" })],
          folders: [],
          feedStats: undefined,
          allArticles: [],
          selection: { type: "all" },
          showUnreadOnly: true,
        }),
    )

    const map = result.current.result.feedMap
    expect(map.size).toBe(2)
    expect(map.get(20)?.name).toBe("Twenty")
    expect(map.get(99)).toBeUndefined()
  })

  it("orderedFeedIds groups feeds under folders sorted by folder.position then trailing for null folder_id", (): void => {
    const feeds: Feed[] = [
      feed(1, { folder_id: 200, position: 0 }),
      feed(2, { folder_id: 100, position: 0 }),
      feed(3, { folder_id: null }), // trailing
      feed(4, { folder_id: 100, position: 1 }),
      feed(5, { enabled: false, folder_id: 100 }), // disabled — excluded
    ]
    const folders: Folder[] = [folder(200, 1), folder(100, 0)]

    const { result } = renderHook(
      (): ReturnType<typeof useHarness> =>
        useHarness({
          feeds,
          folders,
          feedStats: undefined,
          allArticles: [],
          selection: { type: "all" },
          showUnreadOnly: true,
        }),
    )

    // Folder 100 first (position 0), then folder 200, then trailing.
    // Within a folder the order matches the input feeds order (the impl
    // does not re-sort by feed.position).
    expect(result.current.result.orderedFeedIds).toEqual([2, 4, 1, 3])
  })

  it("orderedFeedIds is empty until both feeds and folders are loaded", (): void => {
    const { result } = renderHook(
      (): ReturnType<typeof useHarness> =>
        useHarness({
          feeds: [feed(1)],
          folders: undefined,
          feedStats: undefined,
          allArticles: [],
          selection: { type: "all" },
          showUnreadOnly: true,
        }),
    )

    expect(result.current.result.orderedFeedIds).toEqual([])
  })

  it("enabledFeedIds reflects only enabled feeds", (): void => {
    const feeds: Feed[] = [
      feed(1, { enabled: true, summarize: true }),
      feed(2, { enabled: false, summarize: true }),
      feed(3, { enabled: true, summarize: false }),
    ]

    const { result } = renderHook(
      (): ReturnType<typeof useHarness> =>
        useHarness({
          feeds,
          folders: [],
          feedStats: undefined,
          allArticles: [],
          selection: { type: "all" },
          showUnreadOnly: true,
        }),
    )

    expect([...(result.current.result.enabledFeedIds ?? [])].sort()).toEqual([
      1, 3,
    ])
  })

  it("enabledFeedIds is null when feeds are not yet loaded so downstream can skip filtering", (): void => {
    const { result } = renderHook(
      (): ReturnType<typeof useHarness> =>
        useHarness({
          feeds: undefined,
          folders: [],
          feedStats: undefined,
          allArticles: [article("a", 1)],
          selection: { type: "all" },
          showUnreadOnly: true,
        }),
    )

    expect(result.current.result.enabledFeedIds).toBeNull()
    // enabledArticles passes through unchanged when filter is unknown.
    expect(result.current.result.enabledArticles.map((a) => a.url)).toEqual([
      "a",
    ])
  })
})

describe("useArticleDerivedState — unreadCounts (stats minus session-local reads)", (): void => {
  it("subtracts session-read tally from feedStats unread_count, clamps at 0, drops zero entries", (): void => {
    const feeds: Feed[] = [feed(1), feed(2), feed(3, { enabled: false })]
    const feedStats: Record<string, FeedStats> = {
      "1": {
        article_count: 10,
        unread_count: 3,
        latest_published: null,
        oldest_published: null,
      },
      "2": {
        article_count: 10,
        unread_count: 1,
        latest_published: null,
        oldest_published: null,
      },
      // Disabled feed is excluded from the output.
      "3": {
        article_count: 5,
        unread_count: 5,
        latest_published: null,
        oldest_published: null,
      },
    }
    const allArticles: Article[] = [
      article("a1", 1), // unread
      article("a2", 1), // unread
      article("b1", 2), // unread
    ]

    const { result } = renderHook(
      (): ReturnType<typeof useHarness> =>
        useHarness({
          feeds,
          folders: [],
          feedStats,
          allArticles,
          selection: { type: "all" },
          showUnreadOnly: true,
          // Two session-reads on feed 1, one (over-count) on feed 2.
          // Feed 2 only shows one unread article so its tally is 1 — when
          // subtracted from unread_count=1 the result is 0 and the entry
          // drops out (verifying both the clamp and the "drop zero" path).
          sessionReadSeed: ["a1", "a2", "b1"],
          localReadCount: 3,
        }),
    )

    const counts = result.current.result.unreadCounts
    expect(counts.get(1)).toBe(1) // 3 - 2
    expect(counts.has(2)).toBe(false) // 1 - 1 = 0 → dropped
    expect(counts.has(3)).toBe(false) // disabled
  })

  it("re-tallies sessionReadByFeed when localReadCount ticks even though the ref identity is stable", (): void => {
    const feeds: Feed[] = [feed(1)]
    const allArticles: Article[] = [article("a1", 1), article("a2", 1)]
    const sessionRef: { current: Set<string> } = { current: new Set<string>() }

    const { result, rerender } = renderHook(
      ({ count }: { count: number }) =>
        useArticleDerivedState({
          feeds,
          folders: [],
          feedStats: {
            "1": {
              article_count: 2,
              unread_count: 2,
              latest_published: null,
              oldest_published: null,
            },
          },
          allArticles,
          selection: { type: "all" },
          showUnreadOnly: true,
          sessionReadUrls: sessionRef,
          localReadCount: count,
        }),
      { initialProps: { count: 0 } },
    )

    expect(result.current.unreadCounts.get(1)).toBe(2)

    // Mutate the ref (opaque to React) and bump the counter — the memo
    // should re-run and reflect the new tally.
    act((): void => {
      sessionRef.current.add("a1")
    })
    rerender({ count: 1 })

    expect(result.current.unreadCounts.get(1)).toBe(1)
  })
})

describe("useArticleDerivedState — filtered (selection × unread × session reads)", (): void => {
  it("selection=feed restricts the list to that feed and drops items from others", (): void => {
    const allArticles: Article[] = [
      article("a", 1),
      article("b", 2),
      article("c", 1),
    ]

    const { result } = renderHook(
      (): ReturnType<typeof useHarness> =>
        useHarness({
          feeds: [feed(1), feed(2)],
          folders: [],
          feedStats: undefined,
          allArticles,
          selection: { type: "feed", id: 1 },
          showUnreadOnly: false,
        }),
    )

    expect(result.current.result.filtered.map((a) => a.url)).toEqual(["a", "c"])
  })

  it("selection=folder groups by feed position within that folder and excludes other folders", (): void => {
    const feeds: Feed[] = [
      feed(1, { folder_id: 10, position: 1 }),
      feed(2, { folder_id: 10, position: 0 }),
      feed(3, { folder_id: 20 }),
    ]
    // Articles in arbitrary order — the impl re-sorts by feed position
    // within the folder.
    const allArticles: Article[] = [
      article("f1-x", 1),
      article("f3-x", 3), // excluded (different folder)
      article("f2-x", 2),
      article("f1-y", 1),
    ]

    const { result } = renderHook(
      (): ReturnType<typeof useHarness> =>
        useHarness({
          feeds,
          folders: [folder(10, 0), folder(20, 1)],
          feedStats: undefined,
          allArticles,
          selection: { type: "folder", id: 10 },
          showUnreadOnly: false,
        }),
    )

    // Feed 2 (position 0) comes before feed 1 (position 1).
    expect(result.current.result.filtered.map((a) => a.url)).toEqual([
      "f2-x",
      "f1-x",
      "f1-y",
    ])
  })

  it("showUnreadOnly keeps session-read items so the user can still see what they just dismissed", (): void => {
    const allArticles: Article[] = [
      article("a", 1, { read_at: null }),
      article("b", 1, { read_at: "2024-01-01T00:00:00Z" }), // already read on server
      article("c", 1, { read_at: null }),
    ]

    const { result } = renderHook(
      (): ReturnType<typeof useHarness> =>
        useHarness({
          feeds: [feed(1)],
          folders: [],
          feedStats: undefined,
          allArticles,
          selection: { type: "all" },
          showUnreadOnly: true,
          // `a` was just session-read — should still appear, server-read `b` should not.
          sessionReadSeed: ["a"],
          localReadCount: 1,
        }),
    )

    expect(result.current.result.filtered.map((a) => a.url)).toEqual(["a", "c"])
  })

  it("disabled feeds are stripped from enabledArticles before the selection filter runs", (): void => {
    // selection=all with one feed disabled — articles from the disabled feed
    // must not surface in `filtered` regardless of showUnreadOnly.
    const allArticles: Article[] = [article("on", 1), article("off", 2)]

    const { result } = renderHook(
      (): ReturnType<typeof useHarness> =>
        useHarness({
          feeds: [feed(1, { enabled: true }), feed(2, { enabled: false })],
          folders: [],
          feedStats: undefined,
          allArticles,
          selection: { type: "all" },
          showUnreadOnly: false,
        }),
    )

    expect(result.current.result.filtered.map((a) => a.url)).toEqual(["on"])
  })
})
