import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { api, faviconUrl } from "../api/client"
import type { Feed, Folder, Selection } from "../api/client"

interface Props {
  selection: Selection
  onSelect: (selection: Selection) => void
  unreadCounts: Map<number, number>
}

export default function Sidebar({ selection, onSelect, unreadCounts }: Props) {
  const { data: feeds } = useQuery({
    queryKey: ["feeds"],
    queryFn: api.getFeeds,
  })
  const { data: folders } = useQuery({
    queryKey: ["folders"],
    queryFn: api.getFolders,
  })

  const [collapsed, setCollapsed] = useState<Set<number>>(new Set())

  const enabledFeeds = feeds?.filter((f: Feed) => f.enabled) ?? []
  const totalUnread = Array.from(unreadCounts.values()).reduce((a, b) => a + b, 0)

  const feedsByFolder = new Map<number | null, Feed[]>()
  for (const feed of enabledFeeds) {
    const key = feed.folder_id
    if (!feedsByFolder.has(key)) feedsByFolder.set(key, [])
    feedsByFolder.get(key)!.push(feed)
  }

  const folderUnread = (folderId: number) => {
    const feeds = feedsByFolder.get(folderId) ?? []
    return feeds.reduce((sum, f) => sum + (unreadCounts.get(f.id) ?? 0), 0)
  }

  const toggleCollapse = (folderId: number) => {
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(folderId)) next.delete(folderId)
      else next.add(folderId)
      return next
    })
  }

  const isActive = (sel: Selection) => {
    if (selection.type !== sel.type) return false
    if (sel.type === "all") return true
    return "id" in sel && "id" in selection && sel.id === selection.id
  }

  const btnClass = (active: boolean) =>
    `flex justify-between items-center w-full px-4 py-2.5 text-sm text-left transition-colors cursor-pointer ${
      active ? "text-accent-text" : "text-text-muted hover:text-text"
    }`

  const badgeClass = (active: boolean) =>
    `text-xs px-2 py-0.5 rounded-full shrink-0 ${
      active ? "bg-accent-bg text-accent-text" : "bg-bg-card text-text-muted"
    }`

  const renderFeed = (feed: Feed) => {
    const unread = unreadCounts.get(feed.id) ?? 0
    const active = isActive({ type: "feed", id: feed.id })
    return (
      <button
        key={feed.id}
        onClick={() => onSelect({ type: "feed", id: feed.id })}
        className={btnClass(active)}
      >
        <span className="flex items-center gap-1.5 truncate mr-2">
          {faviconUrl(feed) && (
            <img src={faviconUrl(feed)!} alt="" className="w-4 h-4 shrink-0" loading="lazy" />
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
      {/* All */}
      <button
        onClick={() => onSelect({ type: "all" })}
        className={btnClass(isActive({ type: "all" }))}
      >
        <span>All</span>
        {totalUnread > 0 && (
          <span className={badgeClass(isActive({ type: "all" }))}>{totalUnread}</span>
        )}
      </button>

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
                className="pl-3 pr-1 py-2.5 text-text-dim text-xs"
              >
                {isOpen ? "\u25BE" : "\u25B8"}
              </button>
              <button
                onClick={() => onSelect({ type: "folder", id: folder.id })}
                className={`flex-1 flex justify-between items-center pr-4 py-2.5 text-sm text-left transition-colors cursor-pointer ${
                  folderActive ? "text-accent-text font-medium" : "text-text-muted hover:text-text"
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
