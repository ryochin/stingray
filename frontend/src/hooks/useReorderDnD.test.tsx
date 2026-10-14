/**
 * Reordering rules. The hook decides *what* the new order is; the screens
 * only forward the result to the mutation, so these are the only tests that
 * pin the ordering arithmetic itself.
 */

import { act, renderHook } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"
import type { Feed, Folder } from "../api/client"
import { makeFeed } from "../test-fixtures"
import type { DragHandlers } from "./useReorderDnD"
import { useReorderDnD } from "./useReorderDnD"

const a: Feed = makeFeed({ id: 1, name: "A", folder_id: 10 })
const b: Feed = makeFeed({ id: 2, name: "B", folder_id: 10 })
const c: Feed = makeFeed({ id: 3, name: "C", folder_id: 10 })
const other: Feed = makeFeed({ id: 4, name: "Other", folder_id: 20 })
const loose: Feed = makeFeed({ id: 5, name: "Loose", folder_id: null })
const loose2: Feed = makeFeed({ id: 6, name: "Loose 2", folder_id: null })

const folderOf = (feed: Feed): number | null => feed.folder_id

function setup(feeds: Feed[]) {
  const onReorder = vi.fn()
  const view = renderHook(() =>
    useReorderDnD<Feed>({ items: feeds, groupOf: folderOf, onReorder }),
  )
  return { onReorder, view }
}

describe("useReorderDnD with groups", (): void => {
  it("moves the dragged item to the drop target's position", (): void => {
    const { onReorder, view } = setup([a, b, c])

    act((): void => view.result.current.handlers.onDragStart(c))
    act((): void => view.result.current.handlers.onDrop(a))

    expect(onReorder).toHaveBeenCalledWith([3, 1, 2])
  })

  it("rejects a drop onto an item in a different group", (): void => {
    const { onReorder, view } = setup([a, b, c, other])

    act((): void => view.result.current.handlers.onDragStart(a))
    act((): void => view.result.current.handlers.onDrop(other))

    expect(onReorder).not.toHaveBeenCalled()
  })

  it("sends only the target group's ids, never the whole list", (): void => {
    const { onReorder, view } = setup([a, other, b, c])

    act((): void => view.result.current.handlers.onDragStart(b))
    act((): void => view.result.current.handlers.onDrop(a))

    expect(onReorder).toHaveBeenCalledWith([2, 1, 3])
  })

  it("treats null as a group of its own", (): void => {
    const { onReorder, view } = setup([loose, a, loose2])

    act((): void => view.result.current.handlers.onDragStart(loose2))
    act((): void => view.result.current.handlers.onDrop(loose))

    expect(onReorder).toHaveBeenCalledWith([6, 5])
  })

  it("highlights the hovered item only within the same group", (): void => {
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

  it("rejects a drop when the source changed group mid-drag", (): void => {
    // A poll can land between dragstart and drop; the drop must follow the
    // group the drag started in, as the hover highlight does.
    const onReorder = vi.fn()
    const view = renderHook(
      ({ feeds }: { feeds: Feed[] }) =>
        useReorderDnD<Feed>({ items: feeds, groupOf: folderOf, onReorder }),
      { initialProps: { feeds: [a, other] } },
    )

    act((): void => view.result.current.handlers.onDragStart(a))
    view.rerender({ feeds: [{ ...a, folder_id: 20 }, other] })
    act((): void => view.result.current.handlers.onDrop(other))

    expect(onReorder).not.toHaveBeenCalled()
  })

  it("uses the latest items after a rerender", (): void => {
    const onReorder = vi.fn()
    const view = renderHook(
      ({ feeds }: { feeds: Feed[] }) =>
        useReorderDnD<Feed>({ items: feeds, groupOf: folderOf, onReorder }),
      { initialProps: { feeds: [a, b] } },
    )
    view.rerender({ feeds: [a, b, c] })

    act((): void => view.result.current.handlers.onDragStart(c))
    act((): void => view.result.current.handlers.onDrop(a))

    expect(onReorder).toHaveBeenCalledWith([3, 1, 2])
  })
})

describe("useReorderDnD without groups", (): void => {
  const alpha: Folder = { id: 1, name: "Alpha", position: 0 }
  const beta: Folder = { id: 2, name: "Beta", position: 1 }
  const gamma: Folder = { id: 3, name: "Gamma", position: 2 }

  function setupFolders(): {
    onReorder: ReturnType<typeof vi.fn>
    handlers: () => DragHandlers<Folder>
    dragOverId: () => number | null
  } {
    const onReorder = vi.fn()
    const view = renderHook(() =>
      useReorderDnD<Folder>({ items: [alpha, beta, gamma], onReorder }),
    )
    return {
      onReorder,
      handlers: (): DragHandlers<Folder> => view.result.current.handlers,
      dragOverId: (): number | null => view.result.current.dragOverId,
    }
  }

  it("reorders the whole list as one group", (): void => {
    const { onReorder, handlers } = setupFolders()

    act((): void => handlers().onDragStart(alpha))
    act((): void => handlers().onDrop(gamma))

    expect(onReorder).toHaveBeenCalledWith([2, 3, 1])
  })

  it("ignores a drag that started outside the list", (): void => {
    // Feeds and folders share the Feeds screen; a feed passing over a folder
    // row must not light it up or reorder anything.
    const { onReorder, handlers, dragOverId } = setupFolders()

    act((): void => handlers().onDragOver(beta))
    expect(dragOverId()).toBeNull()

    act((): void => handlers().onDrop(beta))
    expect(onReorder).not.toHaveBeenCalled()
  })

  it("does not highlight the dragged item itself", (): void => {
    const { handlers, dragOverId } = setupFolders()

    act((): void => handlers().onDragStart(beta))
    act((): void => handlers().onDragOver(beta))

    expect(dragOverId()).toBeNull()
  })
})
