import type { JSX } from "react"
import { useCallback, useMemo, useState } from "react"
import type { Feed, FeedStats, Folder } from "../api/client"
import { useFeedDnD } from "../hooks/useFeedDnD"
import type { EditingState, FeedItemActions } from "./FeedItem"
import FeedItem from "./FeedItem"

interface FeedListProps {
  /** Rows to render, already narrowed by the route's name/error filters. */
  visibleFeeds: Feed[]
  /** The *unfiltered* list. Reordering must see every feed in the folder;
   *  handing it the filtered one silently drops the hidden feeds from the
   *  new order. */
  allFeeds: Feed[] | undefined
  folders: Folder[]
  stats: Record<number, FeedStats> | undefined
  fetchingFeeds: Set<number>
  /** Owned by the route: adding a feed auto-expands its card, so this cannot
   *  live down here. */
  expandedFeeds: Set<number>
  onToggleExpand: (feedId: number) => void
  onRename: (feedId: number, name: string) => void
  onReorder: (ids: number[]) => void
  actions: FeedItemActions
}

/**
 * The folder-grouped feed list. Owns the inline-rename state and the drag
 * reordering, neither of which the route has any other use for.
 */
function FeedList({
  visibleFeeds,
  allFeeds,
  folders,
  stats,
  fetchingFeeds,
  expandedFeeds,
  onToggleExpand,
  onRename,
  onReorder,
  actions,
}: FeedListProps): JSX.Element {
  const [editingFeedId, setEditingFeedId] = useState<number | null>(null)
  const [editFeedName, setEditFeedName] = useState<string>("")

  const { dragOverId, handlers: dndHandlers } = useFeedDnD({
    feeds: allFeeds,
    onReorder,
  })

  const feedsByFolder = useMemo((): Map<number | null, Feed[]> => {
    const map: Map<number | null, Feed[]> = new Map<number | null, Feed[]>()
    for (const feed of visibleFeeds) {
      const key: number | null = feed.folder_id
      if (!map.has(key)) map.set(key, [])
      map.get(key)?.push(feed)
    }
    return map
  }, [visibleFeeds])

  const startEdit = useCallback((feed: Feed): void => {
    setEditingFeedId(feed.id)
    setEditFeedName(feed.name)
  }, [])
  const cancelEdit = useCallback((): void => setEditingFeedId(null), [])
  const commitEdit = useCallback(
    (feed: Feed, name: string): void => {
      onRename(feed.id, name)
      setEditingFeedId(null)
    },
    [onRename],
  )

  // Identical for every row, so build it once per render. Not memoized:
  // FeedItem is not wrapped in React.memo, and `actions.onRulesUpdated` is
  // recreated on every render anyway — memoizing here would only look careful.
  const editing: EditingState = {
    id: editingFeedId,
    name: editFeedName,
    onStart: startEdit,
    onCancel: cancelEdit,
    onCommit: commitEdit,
    onChange: setEditFeedName,
  }

  const renderFeed = (feed: Feed): JSX.Element => (
    <FeedItem
      key={feed.id}
      feed={feed}
      stats={stats?.[feed.id]}
      folders={folders}
      fetching={fetchingFeeds.has(feed.id)}
      editing={editing}
      expanded={{
        open: expandedFeeds.has(feed.id),
        onToggle: onToggleExpand,
      }}
      dnd={{ dragOver: dragOverId === feed.id, ...dndHandlers }}
      actions={actions}
    />
  )

  const uncategorized: Feed[] = feedsByFolder.get(null) ?? []

  return (
    <div className="space-y-4">
      {/* Uncategorized first so newly added (uncategorized) feeds, which
          get the smallest position, surface at the very top. */}
      {uncategorized.length > 0 && (
        <div>
          {folders.length > 0 && (
            <h3 className="text-base font-semibold text-text-heading mb-2">
              Uncategorized
            </h3>
          )}
          <div className={`space-y-2 ${folders.length > 0 ? "ml-2" : ""}`}>
            {uncategorized.map(renderFeed)}
          </div>
        </div>
      )}
      {folders.map((folder: Folder) => {
        const folderFeeds: Feed[] = feedsByFolder.get(folder.id) ?? []
        if (folderFeeds.length === 0) return null
        return (
          <div key={folder.id}>
            <h3 className="text-base font-semibold text-text-heading mb-2">
              {folder.name}
            </h3>
            <div className="space-y-2 ml-2">{folderFeeds.map(renderFeed)}</div>
          </div>
        )
      })}
    </div>
  )
}

export default FeedList
