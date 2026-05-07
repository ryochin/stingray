// Pure selectors/transforms for the Articles view.
// Extracted from routes/Articles.tsx so the logic is testable in isolation.

import type { Article, Feed, FeedStats, Selection } from "../api/client"

// Feeds ordered as they appear under a folder in the sidebar.
// Value = position index within the folder (used as sort key).
export type FolderFeedOrder = Map<number, number>

export function computeFolderFeedOrder(
  selection: Selection,
  feeds: Feed[] | undefined,
): FolderFeedOrder | null {
  if (selection.type !== "folder" || !feeds) return null
  const ordered: Feed[] = feeds
    .filter((f: Feed): boolean => f.folder_id === selection.id && f.enabled)
    .slice()
    .sort((a: Feed, b: Feed): number => a.position - b.position || a.id - b.id)
  return new Map(
    ordered.map((f: Feed, i: number): [number, number] => [f.id, i]),
  )
}

// Filter articles by the current selection (all / folder / feed) and group-sort
// folder-selected articles by feed position. Published order within a feed is
// preserved via stable sort — do not re-sort by published here.
export function selectArticles(
  articles: Article[],
  selection: Selection,
  folderFeedOrder: FolderFeedOrder | null,
): Article[] {
  if (selection.type === "feed") {
    return articles.filter((a: Article): boolean => a.feed_id === selection.id)
  }
  if (selection.type === "folder" && folderFeedOrder) {
    const inFolder: Article[] = articles.filter(
      (a: Article): boolean =>
        a.feed_id != null && folderFeedOrder.has(a.feed_id),
    )
    return inFolder.slice().sort((a: Article, b: Article): number => {
      const ai: number = folderFeedOrder.get(a.feed_id as number) ?? 0
      const bi: number = folderFeedOrder.get(b.feed_id as number) ?? 0
      return ai - bi
    })
  }
  return articles
}

// Unread filter keeps items read during the current session so the user can
// still see what they just dismissed with j/m.
export function applyUnreadFilter(
  articles: Article[],
  showUnreadOnly: boolean,
  sessionReadUrls: ReadonlySet<string>,
): Article[] {
  if (!showUnreadOnly) return articles
  return articles.filter(
    (a: Article): boolean => a.read_at == null || sessionReadUrls.has(a.url),
  )
}

// Time range filter keyed by a stable string id so sessionStorage / <select>
// values stay robust against malformed input. `"all"` disables filtering.
export type TimeRangeId = "1d" | "3d" | "7d" | "14d" | "30d" | "all"

export interface TimeRangeOption {
  id: TimeRangeId
  label: string
  days: number | null
}

export const TIME_RANGE_OPTIONS: readonly TimeRangeOption[] = [
  { id: "1d", label: "24 hours", days: 1 },
  { id: "3d", label: "3 days", days: 3 },
  { id: "7d", label: "1 week", days: 7 },
  { id: "14d", label: "2 weeks", days: 14 },
  { id: "30d", label: "30 days", days: 30 },
  { id: "all", label: "All", days: null },
]

const TIME_RANGE_IDS: ReadonlySet<TimeRangeId> = new Set(
  TIME_RANGE_OPTIONS.map((o: TimeRangeOption): TimeRangeId => o.id),
)

// Coerce arbitrary input (sessionStorage, select onChange) into a known id.
// Falls back to "all" — the widest/safest behavior — for anything unknown.
export function parseTimeRangeId(input: unknown): TimeRangeId {
  return typeof input === "string" && TIME_RANGE_IDS.has(input as TimeRangeId)
    ? (input as TimeRangeId)
    : "all"
}

export function timeRangeDays(id: TimeRangeId): number | null {
  return (
    TIME_RANGE_OPTIONS.find((o: TimeRangeOption): boolean => o.id === id)
      ?.days ?? null
  )
}

// Per-feed tally of session-local read URLs that the DB hasn't caught up
// with yet. Only articles still showing `read_at == null` in the cached
// list contribute: once the refetch after `flushReads` lands and `read_at`
// is populated, `feed-stats` already reflects that read, so subtracting
// here too would double-count.
export function tallySessionReadByFeed(
  articles: readonly Article[],
  sessionReadUrls: ReadonlySet<string>,
): Map<number, number> {
  const tally: Map<number, number> = new Map<number, number>()
  for (const a of articles) {
    if (a.feed_id != null && a.read_at == null && sessionReadUrls.has(a.url)) {
      tally.set(a.feed_id, (tally.get(a.feed_id) ?? 0) + 1)
    }
  }
  return tally
}

// Sidebar unread badges: stats from the DB, restricted to enabled feeds (the
// only ones the sidebar shows), minus session-local reads not yet reflected
// in stats. Clamped at 0 to absorb the rare race where stats arrives stale.
export function deriveUnreadCounts(
  feedStats: Record<string, FeedStats> | undefined,
  enabledFeedIds: ReadonlySet<number> | null,
  sessionReadByFeed: ReadonlyMap<number, number>,
): Map<number, number> {
  const map: Map<number, number> = new Map<number, number>()
  if (!feedStats || !enabledFeedIds) return map
  for (const [fidStr, s] of Object.entries(feedStats)) {
    const fid: number = Number(fidStr)
    if (!enabledFeedIds.has(fid)) continue
    const local: number = sessionReadByFeed.get(fid) ?? 0
    const count: number = Math.max(0, s.unread_count - local)
    if (count > 0) map.set(fid, count)
  }
  return map
}

// Advance to the next feed in sidebar order that actually has unread items.
// Returns null if there is no such feed (end of list or current id not in list).
export function nextUnreadFeedId(
  orderedFeedIds: number[],
  currentId: number,
  unreadCounts: ReadonlyMap<number, number>,
): number | null {
  const idx: number = orderedFeedIds.indexOf(currentId)
  if (idx < 0) return null
  for (let i = idx + 1; i < orderedFeedIds.length; i++) {
    const fid: number = orderedFeedIds[i]
    if ((unreadCounts.get(fid) ?? 0) > 0) return fid
  }
  return null
}
