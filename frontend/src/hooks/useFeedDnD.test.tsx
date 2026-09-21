/**
 * Same-folder reordering rules. The hook decides *what* the new order is; the
 * route only forwards the result to the mutation, so these are the only tests
 * that pin the ordering arithmetic itself.
 */

import { act, renderHook } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"
import type { Feed } from "../api/client"
import { makeFeed } from "../test-fixtures"
import { useFeedDnD } from "./useFeedDnD"

const a: Feed = makeFeed({ id: 1, name: "A", folder_id: 10 })
const b: Feed = makeFeed({ id: 2, name: "B", folder_id: 10 })
const c: Feed = makeFeed({ id: 3, name: "C", folder_id: 10 })
const other: Feed = makeFeed({ id: 4, name: "Other", folder_id: 20 })

function setup(feeds: Feed[]) {
  const onReorder = vi.fn()
  const view = renderHook(() => useFeedDnD({ feeds, onReorder }))
  return { onReorder, view }
}

describe("useFeedDnD", (): void => {
  it("moves the dragged feed to the drop target's position", (): void => {
    const { onReorder, view } = setup([a, b, c])

    act((): void => view.result.current.handlers.onDragStart(c))
    act((): void => view.result.current.handlers.onDrop(a))

    expect(onReorder).toHaveBeenCalledWith([3, 1, 2])
  })

  it("rejects a drop onto a feed in a different folder", (): void => {
    const { onReorder, view } = setup([a, b, c, other])

    act((): void => view.result.current.handlers.onDragStart(a))
    act((): void => view.result.current.handlers.onDrop(other))

    expect(onReorder).not.toHaveBeenCalled()
  })

  it("sends only the target folder's ids, never the whole list", (): void => {
    const { onReorder, view } = setup([a, other, b, c])

    act((): void => view.result.current.handlers.onDragStart(b))
    act((): void => view.result.current.handlers.onDrop(a))

    expect(onReorder).toHaveBeenCalledWith([2, 1, 3])
  })

  it("highlights the hovered feed only within the same folder", (): void => {
    const { view } = setup([a, b, c, other])

    act((): void => view.result.current.handlers.onDragStart(a))

    act((): void => view.result.current.handlers.onDragOver(other))
    expect(view.result.current.dragOverId).toBeNull()

    act((): void => view.result.current.handlers.onDragOver(b))
    expect(view.result.current.dragOverId).toBe(2)

    act((): void => view.result.current.handlers.onDragLeave(b))
    expect(view.result.current.dragOverId).toBeNull()

    act((): void => view.result.current.handlers.onDragOver(c))
    expect(view.result.current.dragOverId).toBe(3)

    act((): void => view.result.current.handlers.onDragEnd())
    expect(view.result.current.dragOverId).toBeNull()
  })
})
