import { useMemo } from "react"
import type { Article, Feed, FeedStats, Folder, Selection } from "../api/client"
import {
  applyUnreadFilter,
  computeFolderFeedOrder,
  deriveUnreadCounts,
  type FolderFeedOrder,
  selectArticles,
  tallySessionReadByFeed,
} from "../utils/articleView"

interface UseArticleDerivedStateInput {
  feeds: Feed[] | undefined
  folders: Folder[] | undefined
  feedStats: Record<string, FeedStats> | undefined
  allArticles: Article[] | undefined
  selection: Selection
  showUnreadOnly: boolean
  /** Stable ref tracking URLs marked read in-session. Mutations are opaque
   *  to React; `localReadCount` is what actually invalidates the memos. */
  sessionReadUrls: { readonly current: Set<string> }
  localReadCount: number
}

interface UseArticleDerivedStateResult {
  feedMap: Map<number, Feed>
  orderedFeedIds: number[]
  enabledFeedIds: Set<number> | null
  enabledArticles: Article[]
  sessionReadByFeed: Map<number, number>
  unreadCounts: Map<number, number>
  folderFeedOrder: FolderFeedOrder | null
  filtered: Article[]
}

/** Pure-ish derivation layer over the article queries + UI state. Kept
 *  separate from `useArticleQueries` so the data graph is easy to follow:
 *  queries flow in, memoised projections flow out. */
export function useArticleDerivedState({
  feeds,
  folders,
  feedStats,
  allArticles,
  selection,
  showUnreadOnly,
  sessionReadUrls,
  localReadCount,
}: UseArticleDerivedStateInput): UseArticleDerivedStateResult {
  const feedMap = useMemo((): Map<number, Feed> => {
    const map: Map<number, Feed> = new Map<number, Feed>()
    for (const feed of feeds ?? []) map.set(feed.id, feed)
    return map
  }, [feeds])

  // Ordered feed list matching sidebar display order
  const orderedFeedIds = useMemo((): number[] => {
    if (!feeds || !folders) return []
    const enabled: Feed[] = feeds.filter((feed: Feed): boolean => feed.enabled)
    const sortedFolders = folders
      .slice()
      .sort(
        (folderA, folderB): number =>
          folderA.position - folderB.position || folderA.id - folderB.id,
      )
    const ids: number[] = []
    for (const folder of sortedFolders) {
      for (const feed of enabled) {
        if (feed.folder_id === folder.id) ids.push(feed.id)
      }
    }
    for (const feed of enabled) {
      if (feed.folder_id == null) ids.push(feed.id)
    }
    return ids
  }, [feeds, folders])

  const enabledFeedIds = useMemo((): Set<number> | null => {
    if (!feeds) return null
    return new Set(
      feeds
        .filter((feed: Feed): boolean => feed.enabled)
        .map((feed: Feed): number => feed.id),
    )
  }, [feeds])

  const enabledArticles = useMemo((): Article[] => {
    if (!enabledFeedIds) return allArticles ?? []
    return (allArticles ?? []).filter(
      (article): boolean =>
        article.feed_id != null && enabledFeedIds.has(article.feed_id),
    )
  }, [allArticles, enabledFeedIds])

  // Decrement stats-derived unread counts by URLs the user just marked read
  // locally, so the sidebar reflects the change before the next stats refetch.
  // biome-ignore lint/correctness/useExhaustiveDependencies: `sessionReadUrls` is a stable ref; mutations are opaque to React and tracked via `localReadCount`.
  const sessionReadByFeed = useMemo((): Map<number, number> => {
    void localReadCount
    return tallySessionReadByFeed(enabledArticles, sessionReadUrls.current)
  }, [enabledArticles, localReadCount])

  const unreadCounts = useMemo(
    (): Map<number, number> =>
      deriveUnreadCounts(feedStats, enabledFeedIds, sessionReadByFeed),
    [feedStats, enabledFeedIds, sessionReadByFeed],
  )

  const folderFeedOrder = useMemo(
    (): FolderFeedOrder | null => computeFolderFeedOrder(selection, feeds),
    [selection, feeds],
  )

  // biome-ignore lint/correctness/useExhaustiveDependencies: `sessionReadUrls` is a stable ref; mutations are opaque to React and tracked via `localReadCount`.
  const filtered = useMemo((): Article[] => {
    void localReadCount // sessionReadUrls mutations are opaque to React; re-run when they tick.
    const selected = selectArticles(enabledArticles, selection, folderFeedOrder)
    // Time-range filtering happens at the API layer (sinceDays). The list we
    // got is already bounded; here we only layer the unread toggle on top.
    return applyUnreadFilter(selected, showUnreadOnly, sessionReadUrls.current)
  }, [
    enabledArticles,
    selection,
    folderFeedOrder,
    showUnreadOnly,
    localReadCount,
  ])

  return {
    feedMap,
    orderedFeedIds,
    enabledFeedIds,
    enabledArticles,
    sessionReadByFeed,
    unreadCounts,
    folderFeedOrder,
    filtered,
  }
}
