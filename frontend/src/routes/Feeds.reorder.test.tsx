/**
 * Drag reordering must always send the *whole* folder, even when the name
 * filter is hiding part of it. The hook that computes the new order takes the
 * feed list as an argument, so this contract lives at the call site, not in
 * the hook — only a route-level test can pin it. The same goes for keeping a
 * drop inside one folder, which the call site opts into.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import type { JSX } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { NoopObserver } from "../test-fixtures"
import Feeds from "./Feeds"

vi.mock("../components/Header", () => ({
  default: (): JSX.Element | null => null,
}))

const reorderFeeds = vi.fn()

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>()
  const { makeFeed: make } = await import("../test-fixtures")
  return {
    ...actual,
    api: {
      getStatus: vi.fn().mockResolvedValue({
        running: false,
        last_started_at: null,
        last_finished_at: null,
        last_status: null,
        last_new_count: null,
        last_error: null,
        llm_enabled: false,
        llm_available: false,
        llm_error: null,
      }),
      getFeeds: vi
        .fn()
        .mockResolvedValue([
          make({ id: 1, name: "Alpha" }),
          make({ id: 2, name: "Bravo" }),
          make({ id: 3, name: "Charlie" }),
          make({ id: 4, name: "Delta", folder_id: 10 }),
        ]),
      getFolders: vi
        .fn()
        .mockResolvedValue([{ id: 10, name: "Inbox", position: 0 }]),
      getFeedStats: vi.fn().mockResolvedValue({}),
      reorderFeeds: (...args: unknown[]): unknown => reorderFeeds(...args),
    },
  }
})

function renderFeeds(): void {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <Feeds />
    </QueryClientProvider>,
  )
}

/** The row wrapper is the drop target; the ⠿ handle inside it starts the drag. */
function rowFor(name: string): HTMLElement {
  return screen.getByText(name).closest('[role="listitem"]') as HTMLElement
}

function dragHandleIn(row: HTMLElement): HTMLElement {
  return row.querySelector('[title="Drag to reorder"]') as HTMLElement
}

function dragOnto(source: string, target: string): void {
  // setCardDragImage reaches into dataTransfer, which happy-dom does not
  // synthesize for us.
  fireEvent.dragStart(dragHandleIn(rowFor(source)), {
    dataTransfer: { setDragImage: vi.fn() },
  })
  fireEvent.drop(rowFor(target))
}

beforeEach((): void => {
  vi.stubGlobal("IntersectionObserver", NoopObserver)
  vi.stubGlobal("ResizeObserver", NoopObserver)
  reorderFeeds.mockReset()
  reorderFeeds.mockResolvedValue(undefined)
})

afterEach((): void => {
  vi.unstubAllGlobals()
  document.body.innerHTML = ""
})

describe("Feeds drag reordering", (): void => {
  it("sends the folder's new order when no filter is active", async (): Promise<void> => {
    renderFeeds()
    await screen.findByText("Charlie")

    dragOnto("Charlie", "Alpha")

    await waitFor((): void =>
      expect(reorderFeeds).toHaveBeenCalledWith([3, 1, 2]),
    )
  })

  it("includes feeds hidden by the filter in the new order", async (): Promise<void> => {
    renderFeeds()
    await screen.findByText("Charlie")

    // "l" matches Alpha and Charlie but not Bravo. Bravo stays part of the
    // folder and must keep its slot in the order sent to the server —
    // reordering the filtered list would silently drop it.
    fireEvent.change(screen.getByPlaceholderText("Filter by name or URL..."), {
      target: { value: "l" },
    })
    await waitFor((): void => expect(screen.queryByText("Bravo")).toBeNull())

    dragOnto("Charlie", "Alpha")

    await waitFor((): void =>
      expect(reorderFeeds).toHaveBeenCalledWith([3, 1, 2]),
    )
  })

  it("ignores a drop onto a feed in another folder", async (): Promise<void> => {
    renderFeeds()
    await screen.findByText("Delta")

    dragOnto("Charlie", "Delta")

    // The drop is synchronous; give a stray mutation the chance to fire.
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(reorderFeeds).not.toHaveBeenCalled()
  })
})
