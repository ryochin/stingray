import { useMemo } from "react"
import type { Article, Feed, Folder, Selection } from "../api/client"
import { faviconUrl } from "../api/client"

interface SelectionHeaderInfo {
  icon: string | null
  label: string
  feedUrl: string | null
}

interface UseSelectionHeaderInput {
  selection: Selection
  feedMap: Map<number, Feed>
  folders: Folder[] | undefined
  filtered: Article[]
  localReadCount: number
  hasSessionRead: (url: string) => boolean
}

interface UseSelectionHeaderResult {
  /** Title/icon for the current selection, or null when no selection-level
   *  header should be rendered (e.g. selection.type === "all"). */
  selectionHeader: SelectionHeaderInfo | null
  /** Unread count to display next to the "Unread" toggle. Excludes
   *  session-read items so the number reflects what the user actually
   *  still needs to read. */
  selectedUnreadInView: number
}

/** Memoised presentation derivations for the sticky header. Kept separate
 *  from the data-derivation layer so the header's concerns (selection
 *  label, in-view unread tally) don't bleed into the article list memos. */
export function useSelectionHeader({
  selection,
  feedMap,
  folders,
  filtered,
  localReadCount,
  hasSessionRead,
}: UseSelectionHeaderInput): UseSelectionHeaderResult {
  const selectionHeader = useMemo((): SelectionHeaderInfo | null => {
    if (selection.type === "feed") {
      const feed: Feed | undefined = feedMap.get(selection.id)
      if (!feed) return null
      return { icon: faviconUrl(feed), label: feed.name, feedUrl: feed.url }
    }
    if (selection.type === "folder") {
      const label: string | null =
        folders?.find((folder): boolean => folder.id === selection.id)?.name ??
        null
      return label ? { icon: null, label, feedUrl: null } : null
    }
    return null
  }, [selection, feedMap, folders])

  // `filtered` already applied selection + (in unread mode) the unread
  // filter. Session-read items are kept in `filtered` (intentional, so the
  // user can still see what they just dismissed) — exclude them here so
  // the count reflects only true unreads.
  const selectedUnreadInView = useMemo((): number => {
    void localReadCount
    let count: number = 0
    for (const article of filtered) {
      if (article.read_at == null && !hasSessionRead(article.url)) count++
    }
    return count
  }, [filtered, localReadCount, hasSessionRead])

  return { selectionHeader, selectedUnreadInView }
}
