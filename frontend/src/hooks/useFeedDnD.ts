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
interface DragHandlers {
  onDragStart: (feed: Feed) => void
  onDragOver: (feed: Feed) => void
  onDragLeave: (feed: Feed) => void
  onDragEnd: () => void
  onDrop: (target: Feed) => void
}

interface UseFeedDnDResult {
  dragOverId: number | null
  handlers: DragHandlers
}

export function useFeedDnD({ feeds, onReorder }: Options): UseFeedDnDResult {
  const dragSrc = useRef<{ id: number; folderId: number | null } | null>(null)
  const [dragOverId, setDragOverId] = useState<number | null>(null)

  const onDragStart = useCallback((feed: Feed): void => {
    dragSrc.current = { id: feed.id, folderId: feed.folder_id }
  }, [])

  const onDragOver = useCallback((feed: Feed): void => {
    const src = dragSrc.current
    if (!src || src.id === feed.id) return
    if (src.folderId !== feed.folder_id) return
    setDragOverId(feed.id)
  }, [])

  const onDragLeave = useCallback((feed: Feed): void => {
    setDragOverId((prev: number | null): number | null =>
      prev === feed.id ? null : prev,
    )
  }, [])

  const onDragEnd = useCallback((): void => {
    dragSrc.current = null
    setDragOverId(null)
  }, [])

  const onDrop = useCallback(
    (target: Feed): void => {
      const src = dragSrc.current
      dragSrc.current = null
      setDragOverId(null)
      if (!src || src.id === target.id) return
      if (src.folderId !== target.folder_id) return
      const group: Feed[] = (feeds ?? []).filter(
        (f: Feed): boolean => f.folder_id === target.folder_id,
      )
      const ids: number[] = group.map((f: Feed): number => f.id)
      const fromIdx: number = ids.indexOf(src.id)
      const toIdx: number = ids.indexOf(target.id)
      if (fromIdx < 0 || toIdx < 0) return
      ids.splice(fromIdx, 1)
      ids.splice(toIdx, 0, src.id)
      onReorder(ids)
    },
    [feeds, onReorder],
  )

  return {
    dragOverId,
    handlers: { onDragStart, onDragOver, onDragLeave, onDragEnd, onDrop },
  }
}
