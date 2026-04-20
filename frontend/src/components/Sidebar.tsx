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
  const { data: feeds } = useQuery({
    queryKey: ["feeds"],
    queryFn: api.getFeeds,
    refetchInterval: 15_000,
  })
  const { data: folders } = useQuery({
    queryKey: ["folders"],
    queryFn: api.getFolders,
    refetchInterval: 15_000,
  })

  const [collapsed, setCollapsed] = useState<Set<number>>(() => {
    try {
      const saved = sessionStorage.getItem("collapsed-folders")
      if (saved) return new Set(JSON.parse(saved) as number[])
    } catch {}
    return new Set()
  })

  const enabledFeeds = feeds?.filter((f) => f.enabled) ?? []
  const totalUnreadCount = totalUnread(unreadCounts)
  const feedsByFolder = groupFeedsByFolder(feeds)

  // Auto-expand folder containing the selected feed.
  // The "is it currently collapsed?" check lives inside the setter so we don't
  // need `collapsed` in the dependency list (which would re-trigger every toggle).
  useEffect(() => {
    if (selection.type !== "feed") return
    const selectedFeed = enabledFeeds.find((feed) => feed.id === selection.id)
    const folderId = selectedFeed?.folder_id
    if (folderId == null) return
    setCollapsed((prev) => {
      if (!prev.has(folderId)) return prev
      const next = new Set(prev)
      next.delete(folderId)
      sessionStorage.setItem("collapsed-folders", JSON.stringify([...next]))
      return next
    })
  }, [selection, enabledFeeds])

  const folderUnread = (folderId: number) =>
    folderUnreadCount(feedsByFolder, unreadCounts, folderId)

  const toggleCollapse = (folderId: number) => {
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
        onClick={() => onSelect({ type: "feed", id: feed.id })}
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
