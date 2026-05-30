/**
 * Guards that the empty unread state in <Articles> wires the caught-up
 * sub-label through to <CaughtUpIndicator>. The controller's jump/end
 * derivation and Space gating are covered by the controller+keyboard
 * integration tests; this one specifically catches the wiring omission
 * (forgetting `subLabel` / `ref` on the empty-state indicator) that the
 * integration harness — which attaches the sentinel by hand — cannot.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import type { JSX } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import Articles from "./Articles"

// Trim the render surface: the route's chrome is irrelevant to the empty
// caught-up branch and would otherwise drag in refresh polling / sidebar data.
vi.mock("../components/Header", () => ({
  default: (): JSX.Element | null => null,
}))
vi.mock("../components/Sidebar", () => ({
  default: (): JSX.Element | null => null,
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

function renderArticles(): void {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <Articles />
    </QueryClientProvider>,
  )
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

describe("Articles empty caught-up state", (): void => {
  it("renders the caught-up sub-label on a zero-unread view", async (): Promise<void> => {
    renderArticles()
    // Default selection is "all" with no other unread feed, so the derived
    // hint is "end". The sub-label rendering proves Articles forwards
    // `subLabel` to the empty-state indicator.
    expect(await screen.findByText("No more unread feeds")).toBeTruthy()
  })
})
