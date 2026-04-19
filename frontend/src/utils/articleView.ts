// Pure selectors/transforms for the Articles view.
// Extracted from routes/Articles.tsx so the logic is testable in isolation.

import type { Article, Feed, Selection } from "../api/client"

// Feeds ordered as they appear under a folder in the sidebar.
// Value = position index within the folder (used as sort key).
export type FolderFeedOrder = Map<number, number>

export function computeFolderFeedOrder(
  selection: Selection,
  feeds: Feed[] | undefined,
): FolderFeedOrder | null {
  if (selection.type !== "folder" || !feeds) return null
  const ordered = feeds
    .filter((f) => f.folder_id === selection.id && f.enabled)
    .slice()
    .sort((a, b) => a.position - b.position || a.id - b.id)
  return new Map(ordered.map((f, i) => [f.id, i]))
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
    return articles.filter((a) => a.feed_id === selection.id)
  }
  if (selection.type === "folder" && folderFeedOrder) {
    const inFolder = articles.filter(
      (a) => a.feed_id != null && folderFeedOrder.has(a.feed_id),
    )
    return inFolder.slice().sort((a, b) => {
      const ai = folderFeedOrder.get(a.feed_id as number) ?? 0
      const bi = folderFeedOrder.get(b.feed_id as number) ?? 0
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
  return articles.filter((a) => a.read_at == null || sessionReadUrls.has(a.url))
}

// Advance to the next feed in sidebar order that actually has unread items.
// Returns null if there is no such feed (end of list or current id not in list).
export function nextUnreadFeedId(
  orderedFeedIds: number[],
  currentId: number,
  unreadCounts: ReadonlyMap<number, number>,
): number | null {
  const idx = orderedFeedIds.indexOf(currentId)
  if (idx < 0) return null
  for (let i = idx + 1; i < orderedFeedIds.length; i++) {
    const fid = orderedFeedIds[i]
    if ((unreadCounts.get(fid) ?? 0) > 0) return fid
  }
  return null
}
