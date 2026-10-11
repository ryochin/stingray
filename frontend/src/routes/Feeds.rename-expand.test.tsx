/**
 * Per-row expand and inline rename. These two pieces of state are the ones
 * that move when the feed list is extracted from the route, and the auto-expand
 * on add is the one that must *not* move: it is driven from the add form, not
 * from a row. None of it is covered by types — a misplaced state hook keeps
 * compiling and silently stops working.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import type { JSX } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { makeFeed, NoopObserver } from "../test-fixtures"
import Feeds from "./Feeds"

vi.mock("../components/Header", () => ({
  default: (): JSX.Element | null => null,
}))

const renameFeed = vi.fn()
const createFeed = vi.fn()
const getFeeds = vi.fn()

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>()
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
      getFeeds: (): unknown => getFeeds(),
      getFolders: vi.fn().mockResolvedValue([]),
      getFeedStats: vi.fn().mockResolvedValue({}),
      renameFeed: (...args: unknown[]): unknown => renameFeed(...args),
      createFeed: (...args: unknown[]): unknown => createFeed(...args),
    },
  }
})

function renderFeeds(): QueryClient {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <Feeds />
    </QueryClientProvider>,
  )
  return client
}

/** The expanded card is the only place "Site URL:" appears. */
function isExpanded(): boolean {
  return screen.queryByText("Site URL:") !== null
}

beforeEach((): void => {
  vi.stubGlobal("IntersectionObserver", NoopObserver)
  vi.stubGlobal("ResizeObserver", NoopObserver)
  renameFeed.mockReset()
  renameFeed.mockResolvedValue(undefined)
  createFeed.mockReset()
  getFeeds.mockReset()
  getFeeds.mockResolvedValue([makeFeed({ id: 1, name: "Alpha" })])
})

afterEach((): void => {
  vi.unstubAllGlobals()
  document.body.innerHTML = ""
})

describe("Feeds row expand and rename", (): void => {
  it("expands and collapses a feed card on click", async (): Promise<void> => {
    renderFeeds()
    const name: HTMLElement = await screen.findByText("Alpha")
    expect(isExpanded()).toBe(false)

    fireEvent.click(name)
    await waitFor((): void => expect(isExpanded()).toBe(true))

    fireEvent.click(screen.getByText("Alpha"))
    await waitFor((): void => expect(isExpanded()).toBe(false))
  })

  it("renames the feed on Enter and leaves edit mode", async (): Promise<void> => {
    renderFeeds()
    await screen.findByText("Alpha")

    fireEvent.click(screen.getByTitle("Rename"))
    const input = (await screen.findByDisplayValue("Alpha")) as HTMLInputElement

    fireEvent.change(input, { target: { value: "Renamed" } })
    fireEvent.keyDown(input, { key: "Enter" })

    await waitFor((): void =>
      expect(renameFeed).toHaveBeenCalledWith(1, "Renamed"),
    )
    await waitFor((): void =>
      expect(screen.queryByDisplayValue("Renamed")).toBeNull(),
    )
  })

  it("discards the rename on Escape", async (): Promise<void> => {
    renderFeeds()
    await screen.findByText("Alpha")

    fireEvent.click(screen.getByTitle("Rename"))
    const input = (await screen.findByDisplayValue("Alpha")) as HTMLInputElement

    fireEvent.change(input, { target: { value: "Discarded" } })
    fireEvent.keyDown(input, { key: "Escape" })

    await waitFor((): void => expect(screen.getByText("Alpha")).toBeTruthy())
    expect(renameFeed).not.toHaveBeenCalled()
  })

  it("keeps an in-progress rename across a failed refetch", async (): Promise<void> => {
    // The rename draft lives in FeedList; unmounting the list on a background
    // refetch error would silently throw it away.
    const client: QueryClient = renderFeeds()
    await screen.findByText("Alpha")

    fireEvent.click(screen.getByTitle("Rename"))
    const input = (await screen.findByDisplayValue("Alpha")) as HTMLInputElement
    fireEvent.change(input, { target: { value: "Draft" } })

    getFeeds.mockRejectedValue(new Error("offline"))
    await client.refetchQueries({ queryKey: ["feeds"] })
    await screen.findByText("Failed to load feeds.")
    expect(screen.getByDisplayValue("Draft")).toBeTruthy()

    getFeeds.mockResolvedValue([makeFeed({ id: 1, name: "Alpha" })])
    await client.refetchQueries({ queryKey: ["feeds"] })
    await waitFor((): void =>
      expect(screen.queryByText("Failed to load feeds.")).toBeNull(),
    )
    expect(screen.getByDisplayValue("Draft")).toBeTruthy()
  })

  it("auto-expands the card of a newly added feed", async (): Promise<void> => {
    // Driven by the add form, not by the row — the expanded-set must stay
    // above both. See addFeedAndExpand in the route.
    const added = makeFeed({ id: 2, name: "Added" })
    createFeed.mockResolvedValue(added)
    renderFeeds()
    await screen.findByText("Alpha")

    // The refetch after a successful add is what puts the new row on screen.
    getFeeds.mockResolvedValue([makeFeed({ id: 1, name: "Alpha" }), added])

    const url = screen.getByPlaceholderText(
      "https://example.com/feed",
    ) as HTMLInputElement
    fireEvent.change(url, { target: { value: "https://added.test/feed" } })
    fireEvent.submit(url.closest("form") as HTMLFormElement)

    await screen.findByText("Added")
    await waitFor((): void => expect(isExpanded()).toBe(true))
  })
})
