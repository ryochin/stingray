// Pure selectors for the Sidebar. Extracted from components/Sidebar.tsx so
// grouping and unread-sum logic can be tested without rendering React.

import type { Feed } from "../api/client"

// Group enabled feeds by folder. `folder_id == null` is keyed as the literal
// `null` bucket (Uncategorized). Only enabled feeds are included.
export function groupFeedsByFolder(feeds: Feed[] | undefined): Map<number | null, Feed[]> {
  const map: Map<number | null, Feed[]> = new Map<number | null, Feed[]>()
  for (const feed of feeds ?? []) {
    if (!feed.enabled) continue
    const key: number | null = feed.folder_id
    if (!map.has(key)) map.set(key, [])
    map.get(key)!.push(feed)
  }
  return map
}

export function folderUnreadCount(
  feedsByFolder: ReadonlyMap<number | null, Feed[]>,
  unreadCounts: ReadonlyMap<number, number>,
  folderId: number,
): number {
  const feeds: Feed[] = feedsByFolder.get(folderId) ?? []
  return feeds.reduce((sum: number, f: Feed): number => sum + (unreadCounts.get(f.id) ?? 0), 0)
}

export function totalUnread(unreadCounts: ReadonlyMap<number, number>): number {
  let sum = 0
  for (const v of unreadCounts.values()) sum += v
  return sum
}
