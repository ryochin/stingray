/**
 * The sidebar selection survives a reload through sessionStorage. The stored
 * value is only parsed JSON, so anything not shaped like a Selection must fall
 * back to "all" rather than reach the selection-keyed hooks as-is.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import type { JSX } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { Selection } from "../api/client"
import Articles from "./Articles"

vi.mock("../components/Header", () => ({
  default: (): JSX.Element | null => null,
}))
// Stand-in that exposes the selection Articles hands to the sidebar.
vi.mock("../components/Sidebar", () => ({
  default: ({ selection }: { selection: Selection }): JSX.Element => (
    <output data-testid="selection">{JSON.stringify(selection)}</output>
  ),
}))

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
      getArticles: vi.fn().mockResolvedValue([]),
      getFeeds: vi.fn().mockResolvedValue([]),
      getFolders: vi.fn().mockResolvedValue([]),
      getFeedStats: vi.fn().mockResolvedValue({}),
    },
  }
})

class NoopObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
  takeRecords(): [] {
    return []
  }
}

function restoredSelection(): Selection {
  const client: QueryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <Articles />
    </QueryClientProvider>,
  )
  return JSON.parse(
    screen.getByTestId("selection").textContent ?? "",
  ) as Selection
}

beforeEach((): void => {
  vi.stubGlobal("IntersectionObserver", NoopObserver)
  vi.stubGlobal("ResizeObserver", NoopObserver)
  sessionStorage.clear()
})

afterEach((): void => {
  vi.unstubAllGlobals()
  document.body.innerHTML = ""
})

describe("Articles selection restore", (): void => {
  it("restores a stored feed selection", (): void => {
    sessionStorage.setItem(
      "feed-selection",
      JSON.stringify({ type: "feed", id: 3 }),
    )
    expect(restoredSelection()).toEqual({ type: "feed", id: 3 })
  })

  it.each([
    ["a malformed selection", JSON.stringify({ type: "feed", id: "3" })],
    ["a non-object", JSON.stringify(42)],
    ["invalid JSON", "{"],
  ])("falls back to all for %s", (_label: string, stored: string): void => {
    sessionStorage.setItem("feed-selection", stored)
    expect(restoredSelection()).toEqual({ type: "all" })
  })
})
