import { useCallback, useRef, useState } from "react"

interface Options<T> {
  /** Every item in display order, unfiltered, so a drop keeps hidden ones. */
  items: T[] | undefined
  /** Limits a drop to items of the same group. Without it the whole list is
   *  one group. Pass a stable reference: the handlers depend on it. */
  groupOf?: (item: T) => unknown
  onReorder: (ids: number[]) => void
}

/**
 * Drag-and-drop reordering via native HTML5 events. A drop onto an item of
 * another group is rejected (feeds move between folders through a separate
 * select).
 *
 * `onReorder` receives the new ordered id list for the affected group, which
 * the caller hands to the reorder mutation.
 */
export interface DragHandlers<T> {
  onDragStart: (item: T) => void
  onDragOver: (item: T) => void
  onDragLeave: (item: T) => void
  onDragEnd: () => void
  onDrop: (target: T) => void
}

interface UseReorderDnDResult<T> {
  dragOverId: number | null
  handlers: DragHandlers<T>
}

const SINGLE_GROUP = (): null => null

export function useReorderDnD<T extends { id: number }>({
  items,
  groupOf = SINGLE_GROUP,
  onReorder,
}: Options<T>): UseReorderDnDResult<T> {
  // Only a drag that started in this list counts: another list on the page
  // (feeds beside folders) leaves this null, so its items never highlight.
  const dragSrc = useRef<{ id: number; group: unknown } | null>(null)
  const [dragOverId, setDragOverId] = useState<number | null>(null)

  const onDragStart = useCallback(
    (item: T): void => {
      dragSrc.current = { id: item.id, group: groupOf(item) }
    },
    [groupOf],
  )

  const onDragOver = useCallback(
    (item: T): void => {
      const src = dragSrc.current
      if (!src || src.id === item.id) return
      if (src.group !== groupOf(item)) return
      setDragOverId(item.id)
    },
    [groupOf],
  )

  const onDragLeave = useCallback((item: T): void => {
    setDragOverId((prev: number | null): number | null =>
      prev === item.id ? null : prev,
    )
  }, [])

  const onDragEnd = useCallback((): void => {
    dragSrc.current = null
    setDragOverId(null)
  }, [])

  const onDrop = useCallback(
    (target: T): void => {
      const src = dragSrc.current
      dragSrc.current = null
      setDragOverId(null)
      if (!src || src.id === target.id) return
      // Compare against the group captured at drag start: a refetch during
      // the drag may already have moved the source into the target's group.
      const group: unknown = groupOf(target)
      if (src.group !== group) return
      const ids: number[] = (items ?? [])
        .filter((item: T): boolean => groupOf(item) === group)
        .map((item: T): number => item.id)
      const fromIdx: number = ids.indexOf(src.id)
      const toIdx: number = ids.indexOf(target.id)
      if (fromIdx < 0 || toIdx < 0) return
      ids.splice(fromIdx, 1)
      ids.splice(toIdx, 0, src.id)
      onReorder(ids)
    },
    [items, groupOf, onReorder],
  )

  return {
    dragOverId,
    handlers: { onDragStart, onDragOver, onDragLeave, onDragEnd, onDrop },
  }
}
