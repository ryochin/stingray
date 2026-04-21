import { useCallback, useRef, useState } from "react"
import type { Feed } from "../api/client"

interface Options {
  feeds: Feed[] | undefined
  onReorder: (ids: number[]) => void
}

/**
 * Same-folder feed reordering via HTML5 drag-and-drop. Cross-folder drops are
 * rejected (moving to a different folder goes through a separate select).
 *
 * `onReorder` receives the new ordered id list for the affected folder,
 * which the caller hands to the reorder mutation.
 */
export function useFeedDnD({ feeds, onReorder }: Options) {
  const dragSrc = useRef<{ id: number, folderId: number | null } | null>(null)
  const [dragOverId, setDragOverId] = useState<number | null>(null)

  const onDragStart = useCallback((feed: Feed) => {
    dragSrc.current = { id: feed.id, folderId: feed.folder_id }
  }, [])

  const onDragOver = useCallback((feed: Feed) => {
    const src = dragSrc.current
    if (!src || src.id === feed.id) return
    if (src.folderId !== feed.folder_id) return
    setDragOverId(feed.id)
  }, [])

  const onDragLeave = useCallback((feed: Feed) => {
    setDragOverId((prev) => (prev === feed.id ? null : prev))
  }, [])

  const onDragEnd = useCallback(() => {
    dragSrc.current = null
    setDragOverId(null)
  }, [])

  const onDrop = useCallback((target: Feed) => {
    const src = dragSrc.current
    dragSrc.current = null
    setDragOverId(null)
    if (!src || src.id === target.id) return
    if (src.folderId !== target.folder_id) return
    const group = (feeds ?? []).filter((f) => f.folder_id === target.folder_id)
    const ids = group.map((f) => f.id)
    const fromIdx = ids.indexOf(src.id)
    const toIdx = ids.indexOf(target.id)
    if (fromIdx < 0 || toIdx < 0) return
    ids.splice(fromIdx, 1)
    ids.splice(toIdx, 0, src.id)
    onReorder(ids)
  }, [feeds, onReorder])

  return {
    dragOverId,
    handlers: { onDragStart, onDragOver, onDragLeave, onDragEnd, onDrop },
  }
}
