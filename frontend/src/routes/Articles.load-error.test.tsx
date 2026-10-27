/**
 * Guards that <Articles> surfaces failures of the sidebar-backing queries.
 * The articles query has its own in-list message; feeds, folders and feed
 * stats used to fail silently, leaving stale or missing unread counts.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import type { JSX } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { api } from "../api/client"
import Articles from "./Articles"

// Header reports `status` itself (see Header.status-error.test.tsx), and the
// sidebar would only render the same cached data the banner is about.
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
      getFeedStats: vi.fn().mockRejectedValue(new Error("offline")),
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

describe("Articles load failure banner", (): void => {
  it("names the failed sidebar query above the list", async (): Promise<void> => {
    renderArticles()
    const banner: HTMLElement = await screen.findByRole("alert")
    expect(banner.textContent).toBe("Failed to load feed stats.")
    // The virtualizer offsets the list by the sticky header's height; a
    // banner outside it would shift the list without moving that offset.
    expect(banner.closest(".sticky")).not.toBeNull()
  })

  it("announces a failed article load as an alert", async (): Promise<void> => {
    vi.mocked(api.getArticles).mockRejectedValueOnce(new Error("offline"))
    renderArticles()
    expect(await screen.findByText("Failed to load articles.")).toHaveAttribute(
      "role",
      "alert",
    )
  })
})
