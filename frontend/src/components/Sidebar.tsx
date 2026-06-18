import { useQuery } from "@tanstack/react-query"
import type { JSX, MouseEvent } from "react"
import { useEffect, useState } from "react"
import type { Feed, Folder, Selection } from "../api/client"
import { api, faviconUrl } from "../api/client"
import {
  folderUnreadCount,
  groupFeedsByFolder,
  totalUnread,
} from "../utils/sidebarView"

interface Props {
  selection: Selection
  onSelect: (selection: Selection) => void
  unreadCounts: Map<number, number>
}

export default function Sidebar({
  selection,
  onSelect,
  unreadCounts,
}: Props): JSX.Element {
  // Polling is owned by the parent Articles route; the shared TanStack Query
  // cache means we get the latest data without running redundant timers here.
  const { data: feeds } = useQuery({
    queryKey: ["feeds"],
    queryFn: api.getFeeds,
  })
  const { data: folders } = useQuery({
    queryKey: ["folders"],
    queryFn: api.getFolders,
  })

  const [collapsed, setCollapsed] = useState<Set<number>>((): Set<number> => {
    try {
      const saved: string | null = sessionStorage.getItem("collapsed-folders")
      if (saved) return new Set(JSON.parse(saved) as number[])
    } catch {}
    return new Set()
  })
  // Folders auto-expanded by a selection change. Transient (not persisted).
  // When selection leaves an auto-expanded folder it gets re-collapsed.
  // Promoted to "sticky open" when the user clicks a feed in it or toggles the caret.
  const [autoExpanded, setAutoExpanded] = useState<Set<number>>(
    new Set<number>(),
  )

  const enabledFeeds: Feed[] =
    feeds?.filter((feedItem: Feed): boolean => feedItem.enabled) ?? []
  const totalUnreadCount: number = totalUnread(unreadCounts)
  const feedsByFolder: Map<number | null, Feed[]> = groupFeedsByFolder(feeds)

  useEffect((): void => {
    const selectedFolderId: number | null =
      selection.type === "feed"
        ? (enabledFeeds.find(
            (feedItem: Feed): boolean => feedItem.id === selection.id,
          )?.folder_id ?? null)
        : null

    const toRecollapse: number[] = []
    for (const id of autoExpanded) {
      if (id !== selectedFolderId) toRecollapse.push(id)
    }
    const folderToAutoExpand: number | null =
      selectedFolderId != null &&
      collapsed.has(selectedFolderId) &&
      !autoExpanded.has(selectedFolderId)
        ? selectedFolderId
        : null

    if (toRecollapse.length === 0 && folderToAutoExpand == null) return

    const nextAuto: Set<number> = new Set<number>()
    if (selectedFolderId != null && autoExpanded.has(selectedFolderId))
      nextAuto.add(selectedFolderId)
    if (folderToAutoExpand != null) nextAuto.add(folderToAutoExpand)

    const nextCollapsed: Set<number> = new Set(collapsed)
    for (const id of toRecollapse) nextCollapsed.add(id)
    if (folderToAutoExpand != null) nextCollapsed.delete(folderToAutoExpand)

    setAutoExpanded(nextAuto)
    setCollapsed(nextCollapsed)
    sessionStorage.setItem(
      "collapsed-folders",
      JSON.stringify([...nextCollapsed]),
    )
  }, [selection, enabledFeeds, collapsed, autoExpanded])

  const folderUnread = (folderId: number): number =>
    folderUnreadCount(feedsByFolder, unreadCounts, folderId)

  const promoteToSticky = (folderId: number): void => {
    setAutoExpanded((prev: Set<number>): Set<number> => {
      if (!prev.has(folderId)) return prev
      const next: Set<number> = new Set(prev)
      next.delete(folderId)
      return next
    })
  }

  const toggleCollapse = (folderId: number): void => {
    promoteToSticky(folderId)
    setCollapsed((prev: Set<number>): Set<number> => {
      const next: Set<number> = new Set(prev)
      if (next.has(folderId)) next.delete(folderId)
      else next.add(folderId)
      sessionStorage.setItem("collapsed-folders", JSON.stringify([...next]))
      return next
    })
  }

  // Alt+caret bulk-close. Also keyboard-accessible: focusing a caret and
  // pressing Alt+Enter (or Alt+Space) fires a click event whose `altKey` is
  // true, so the same branch below runs. Clears autoExpanded by design: a
  // folder open because of a feed selection will be re-expanded by the
  // useEffect above (mirrors single-folder collapse for the selected feed).
  const collapseAll = (): void => {
    const allIds: number[] = (folders ?? []).map((f: Folder): number => f.id)
    const next: Set<number> = new Set(allIds)
    setCollapsed(next)
    setAutoExpanded(new Set<number>())
    sessionStorage.setItem("collapsed-folders", JSON.stringify([...next]))
  }

  const isActive = (sel: Selection): boolean => {
    if (selection.type !== sel.type) return false
    if (sel.type === "all") return true
    return "id" in sel && "id" in selection && sel.id === selection.id
  }

  const btnClass = (active: boolean): string =>
    `flex justify-between items-center w-full px-4 py-1.5 text-xs text-left transition-colors cursor-pointer focus:outline-none ${
      active ? "text-accent-text" : "text-[#bcbcbc] hover:text-text"
    }`

  const badgeClass = (active: boolean): string =>
    `text-xs px-2 py-0.5 rounded-full shrink-0 ${
      active ? "bg-accent-bg text-accent-text" : "bg-bg-card text-text-muted"
    }`

  const renderFeed = (feed: Feed): JSX.Element => {
    const unread: number = unreadCounts.get(feed.id) ?? 0
    const active: boolean = isActive({ type: "feed", id: feed.id })
    const favicon: string | null = faviconUrl(feed)
    return (
      <button
        key={feed.id}
        type="button"
        onClick={() => {
          if (feed.folder_id != null) promoteToSticky(feed.folder_id)
          onSelect({ type: "feed", id: feed.id })
        }}
        className={btnClass(active)}
      >
        <span className="flex items-center gap-1.5 truncate mr-2">
          {favicon && (
            <img
              src={favicon}
              alt=""
              className="w-4 h-4 shrink-0"
              loading="lazy"
            />
          )}
          <span className="truncate">{feed.name}</span>
          {feed.consecutive_failures >= 3 && (
            <span
              className="text-yellow-400 text-xs shrink-0"
              title="Feed has consecutive fetch failures"
            >
              !!!
            </span>
          )}
        </span>
        {unread > 0 && <span className={badgeClass(active)}>{unread}</span>}
      </button>
    )
  }

  const sortedFolders: Folder[] = (folders ?? [])
    .slice()
    .sort(
      (a: Folder, b: Folder): number => a.position - b.position || a.id - b.id,
    )
  const uncategorized: Feed[] = feedsByFolder.get(null) ?? []

  return (
    <nav className="w-80 shrink-0 bg-bg-secondary border-r border-border overflow-y-auto py-2">
      {/* All Feeds */}
      <button
        type="button"
        onClick={() => onSelect({ type: "all" })}
        className={btnClass(isActive({ type: "all" }))}
      >
        <span>All Feeds</span>
        {totalUnreadCount > 0 && (
          <span className={badgeClass(isActive({ type: "all" }))}>
            {totalUnreadCount}
          </span>
        )}
      </button>
      <div className="border-b border-border mx-3 my-1" />

      {/* Uncategorized first so newly added (uncategorized) feeds, which get
          the smallest position, surface at the very top. */}
      {uncategorized.length > 0 && (
        <>
          {sortedFolders.length > 0 && (
            <div className="px-4 py-1.5 text-xs text-text-dim">
              Uncategorized
            </div>
          )}
          {uncategorized.map(renderFeed)}
          {sortedFolders.length > 0 && (
            <div className="border-b border-border mx-3 my-1" />
          )}
        </>
      )}

      {/* Folders */}
      {sortedFolders.map((folder: Folder): JSX.Element => {
        const isOpen: boolean = !collapsed.has(folder.id)
        const unread: number = folderUnread(folder.id)
        const folderActive: boolean = isActive({
          type: "folder",
          id: folder.id,
        })
        const folderFeeds: Feed[] = feedsByFolder.get(folder.id) ?? []

        return (
          <div key={folder.id}>
            <div className="flex items-center">
              <button
                type="button"
                aria-label={`${isOpen ? "Collapse" : "Expand"} ${folder.name}`}
                aria-expanded={isOpen}
                onClick={(e: MouseEvent<HTMLButtonElement>): void => {
                  if (e.altKey) collapseAll()
                  else toggleCollapse(folder.id)
                }}
                className="pl-3 pr-1.5 py-1.5 text-text-dim text-base leading-none focus:outline-none"
              >
                {isOpen ? "\u25BE" : "\u25B8"}
              </button>
              <button
                type="button"
                onClick={(): void =>
                  onSelect({ type: "folder", id: folder.id })
                }
                className={`flex-1 flex justify-between items-center pr-4 py-1.5 text-sm text-left transition-colors cursor-pointer focus:outline-none ${
                  folderActive
                    ? "text-accent-text font-medium"
                    : "text-text hover:text-text-heading"
                }`}
              >
                <span className="truncate mr-2">{folder.name}</span>
                {unread > 0 && (
                  <span className={badgeClass(folderActive)}>{unread}</span>
                )}
              </button>
            </div>
            {isOpen &&
              folderFeeds.map(
                (feed: Feed): JSX.Element => (
                  <div key={feed.id} className="pl-4">
                    {renderFeed(feed)}
                  </div>
                ),
              )}
          </div>
        )
      })}
    </nav>
  )
}
