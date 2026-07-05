/**
 * Guards the degraded-vs-failing distinction in the expanded feed card. A feed
 * that served a stale/cached copy (a diagnostic `last_error` but no consecutive
 * failures) is a soft "Stale" warning shown in yellow, while a feed with
 * `consecutive_failures > 0` is a hard red "Error". See task.md.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import type { JSX } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { Feed } from "../api/client"
import Feeds from "./Feeds"

// The route chrome is irrelevant here and would drag in refresh polling.
vi.mock("../components/Header", () => ({
  default: (): JSX.Element | null => null,
}))

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
      getFeeds: (...args: unknown[]): unknown => getFeeds(...args),
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

function makeFeed(overrides: Partial<Feed>): Feed {
  return {
    id: 1,
    name: "Test Feed",
    url: "https://x.test/feed",
    site_url: null,
    translate: false,
    summarize: false,
    enabled: true,
    folder_id: null,
    position: 0,
    last_fetched_at: null,
    consecutive_failures: 0,
    last_error: null,
    // null keeps the card out of the lazy ExtractionRulesEditor (Suspense).
    extraction_rules: null,
    created_at: "2026-07-05T00:00:00Z",
    ...overrides,
  }
}

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

// The `last_error` line lives inside the expanded card details, so expand the
// card by clicking its name before asserting on the diagnostic text.
async function expandFeed(name: string): Promise<void> {
  fireEvent.click(await screen.findByText(name))
}

beforeEach((): void => {
  vi.stubGlobal("IntersectionObserver", NoopObserver)
  vi.stubGlobal("ResizeObserver", NoopObserver)
  getFeeds.mockReset()
})

afterEach((): void => {
  vi.unstubAllGlobals()
  document.body.innerHTML = ""
})

describe("degraded vs failing last_error display", (): void => {
  it("shows a yellow Stale warning for a degraded feed (failures 0 + last_error)", async (): Promise<void> => {
    getFeeds.mockResolvedValue([
      makeFeed({
        name: "Degraded Feed",
        consecutive_failures: 0,
        last_error: "served cached copy",
      }),
    ])
    renderFeeds()
    await expandFeed("Degraded Feed")

    const line: HTMLElement = await screen.findByText(
      "Stale: served cached copy",
    )
    expect(line.className).toContain("text-yellow-400")
    expect(line.className).not.toContain("text-red-400")
    expect(screen.queryByText(/^Error:/)).toBeNull()
  })

  it("shows a red Error for a failing feed (consecutive_failures > 0)", async (): Promise<void> => {
    getFeeds.mockResolvedValue([
      makeFeed({
        name: "Failing Feed",
        consecutive_failures: 3,
        last_error: "net timeout",
      }),
    ])
    renderFeeds()
    await expandFeed("Failing Feed")

    const line: HTMLElement = await screen.findByText("Error: net timeout")
    expect(line.className).toContain("text-red-400")
    expect(line.className).not.toContain("text-yellow-400")
    expect(screen.queryByText(/^Stale:/)).toBeNull()
  })

  it("shows neither Stale nor Error when there is no last_error", async (): Promise<void> => {
    getFeeds.mockResolvedValue([
      makeFeed({
        name: "Healthy Feed",
        consecutive_failures: 0,
        last_error: null,
      }),
    ])
    renderFeeds()
    await expandFeed("Healthy Feed")

    // The expanded details render (Added: ... is always present) but no
    // diagnostic line appears.
    await waitFor((): void => {
      expect(screen.getByText(/^Added:/)).toBeTruthy()
    })
    expect(screen.queryByText(/^Stale:/)).toBeNull()
    expect(screen.queryByText(/^Error:/)).toBeNull()
  })
})
