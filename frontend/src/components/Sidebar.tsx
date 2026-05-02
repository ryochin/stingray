import { useState, useEffect } from "react"
import { useQuery } from "@tanstack/react-query"
import { api, faviconUrl } from "../api/client"
import type { Feed, Folder, Selection } from "../api/client"
import { folderUnreadCount, groupFeedsByFolder, totalUnread } from "../utils/sidebarView"

interface Props {
  selection: Selection
  onSelect: (selection: Selection) => void
  unreadCounts: Map<number, number>
}

export default function Sidebar({ selection, onSelect, unreadCounts }: Props) {
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

  const [collapsed, setCollapsed] = useState<Set<number>>(() => {
    try {
      const saved = sessionStorage.getItem("collapsed-folders")
      if (saved) return new Set(JSON.parse(saved) as number[])
    } catch {}
    return new Set()
  })
  // Folders auto-expanded by a selection change. Transient (not persisted).
  // When selection leaves an auto-expanded folder it gets re-collapsed.
  // Promoted to "sticky open" when the user clicks a feed in it or toggles the caret.
  const [autoExpanded, setAutoExpanded] = useState<Set<number>>(new Set())

  const enabledFeeds = feeds?.filter((f) => f.enabled) ?? []
  const totalUnreadCount = totalUnread(unreadCounts)
  const feedsByFolder = groupFeedsByFolder(feeds)

  useEffect(() => {
    const selectedFolderId =
      selection.type === "feed"
        ? enabledFeeds.find((feed) => feed.id === selection.id)?.folder_id ?? null
        : null

    const toRecollapse: number[] = []
    for (const id of autoExpanded) {
      if (id !== selectedFolderId) toRecollapse.push(id)
    }
    const shouldAutoExpand =
      selectedFolderId != null && collapsed.has(selectedFolderId) && !autoExpanded.has(selectedFolderId)

    if (toRecollapse.length === 0 && !shouldAutoExpand) return

    const nextAuto = new Set<number>()
    if (selectedFolderId != null && autoExpanded.has(selectedFolderId)) nextAuto.add(selectedFolderId)
    if (shouldAutoExpand) nextAuto.add(selectedFolderId)

    const nextCollapsed = new Set(collapsed)
    for (const id of toRecollapse) nextCollapsed.add(id)
    if (shouldAutoExpand) nextCollapsed.delete(selectedFolderId)

    setAutoExpanded(nextAuto)
    setCollapsed(nextCollapsed)
    sessionStorage.setItem("collapsed-folders", JSON.stringify([...nextCollapsed]))
  }, [selection, enabledFeeds, collapsed, autoExpanded])

  const folderUnread = (folderId: number) =>
    folderUnreadCount(feedsByFolder, unreadCounts, folderId)

  const promoteToSticky = (folderId: number) => {
    setAutoExpanded((prev) => {
      if (!prev.has(folderId)) return prev
      const next = new Set(prev)
      next.delete(folderId)
      return next
    })
  }

  const toggleCollapse = (folderId: number) => {
    promoteToSticky(folderId)
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(folderId)) next.delete(folderId)
      else next.add(folderId)
      sessionStorage.setItem("collapsed-folders", JSON.stringify([...next]))
      return next
    })
  }

  const isActive = (sel: Selection) => {
    if (selection.type !== sel.type) return false
    if (sel.type === "all") return true
    return "id" in sel && "id" in selection && sel.id === selection.id
  }

  const btnClass = (active: boolean) =>
    `flex justify-between items-center w-full px-4 py-1.5 text-xs text-left transition-colors cursor-pointer focus:outline-none ${
      active ? "text-accent-text" : "text-text-muted hover:text-text"
    }`

  const badgeClass = (active: boolean) =>
    `text-xs px-2 py-0.5 rounded-full shrink-0 ${
      active ? "bg-accent-bg text-accent-text" : "bg-bg-card text-text-muted"
    }`

  const renderFeed = (feed: Feed) => {
    const unread = unreadCounts.get(feed.id) ?? 0
    const active = isActive({ type: "feed", id: feed.id })
    const favicon = faviconUrl(feed)
    return (
      <button
        key={feed.id}
        onClick={() => {
          if (feed.folder_id != null) promoteToSticky(feed.folder_id)
          onSelect({ type: "feed", id: feed.id })
        }}
        className={btnClass(active)}
      >
        <span className="flex items-center gap-1.5 truncate mr-2">
          {favicon && (
            <img src={favicon} alt="" className="w-4 h-4 shrink-0" loading="lazy" />
          )}
          <span className="truncate">{feed.name}</span>
          {feed.consecutive_failures >= 3 && (
            <span className="text-yellow-400 text-xs shrink-0" title="Feed has consecutive fetch failures">!!!</span>
          )}
        </span>
        {unread > 0 && <span className={badgeClass(active)}>{unread}</span>}
      </button>
    )
  }

  const sortedFolders = (folders ?? []).slice().sort((a, b) => a.position - b.position || a.id - b.id)
  const uncategorized = feedsByFolder.get(null) ?? []

  return (
    <nav className="w-80 shrink-0 bg-bg-secondary border-r border-border overflow-y-auto py-2">
      {/* All Feeds */}
      <button
        onClick={() => onSelect({ type: "all" })}
        className={btnClass(isActive({ type: "all" }))}
      >
        <span>All Feeds</span>
        {totalUnreadCount > 0 && (
          <span className={badgeClass(isActive({ type: "all" }))}>{totalUnreadCount}</span>
        )}
      </button>
      <div className="border-b border-border mx-3 my-1" />

      {/* Folders */}
      {sortedFolders.map((folder: Folder) => {
        const isOpen = !collapsed.has(folder.id)
        const unread = folderUnread(folder.id)
        const folderActive = isActive({ type: "folder", id: folder.id })
        const folderFeeds = feedsByFolder.get(folder.id) ?? []

        return (
          <div key={folder.id}>
            <div className="flex items-center">
              <button
                onClick={() => toggleCollapse(folder.id)}
                className="pl-3 pr-1.5 py-1.5 text-text-dim text-base leading-none focus:outline-none"
              >
                {isOpen ? "\u25BE" : "\u25B8"}
              </button>
              <button
                onClick={() => onSelect({ type: "folder", id: folder.id })}
                className={`flex-1 flex justify-between items-center pr-4 py-1.5 text-sm text-left transition-colors cursor-pointer focus:outline-none ${
                  folderActive ? "text-accent-text font-medium" : "text-text hover:text-text-heading"
                }`}
              >
                <span className="truncate mr-2">{folder.name}</span>
                {unread > 0 && <span className={badgeClass(folderActive)}>{unread}</span>}
              </button>
            </div>
            {isOpen && folderFeeds.map((feed) => (
              <div key={feed.id} className="pl-4">
                {renderFeed(feed)}
              </div>
            ))}
          </div>
        )
      })}

      {/* Uncategorized */}
      {uncategorized.length > 0 && sortedFolders.length > 0 && (
        <div className="border-t border-border mt-1 pt-1">
          <div className="px-4 py-1.5 text-xs text-text-dim">Uncategorized</div>
        </div>
      )}
      {uncategorized.map(renderFeed)}
    </nav>
  )
}
