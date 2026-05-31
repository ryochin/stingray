/**
 * Guards that <Articles> moves DOM focus into the scroll container on mount
 * (and after a feed/filter switch). The app scrolls inside <main>
 * (overflow-y-auto) rather than the document, so without a focused element
 * inside <main> the browser's native Space/PageDown scrolling has no target
 * and pressing Space right after opening the view does nothing. jsdom can't
 * exercise native scrolling, so we assert the precondition that makes it
 * work: <main> is focusable (tabIndex=-1) and holds focus after render.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import type { JSX } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import Articles from "./Articles"

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

describe("Articles scroll-container focus", (): void => {
  it("focuses the scroll container on mount so native Space scrolls", async (): Promise<void> => {
    renderArticles()
    // Let the route settle (empty caught-up state renders).
    await screen.findByText("No more unread feeds")
    const main: HTMLElement = screen.getByRole("main")
    expect(main.getAttribute("tabindex")).toBe("-1")
    expect(document.activeElement).toBe(main)
  })

  it("returns focus to the scroll container after a filter switch", async (): Promise<void> => {
    renderArticles()
    await screen.findByText("No more unread feeds")
    const main: HTMLElement = screen.getByRole("main")
    // Toggling Unread→All moves focus to the clicked button; the reset
    // effect (deps include showUnreadOnly) must pull it back to <main> so
    // native Space scrolling keeps working without re-clicking the list.
    fireEvent.click(screen.getByRole("button", { name: "All" }))
    await waitFor((): void => {
      expect(document.activeElement).toBe(main)
    })
  })
})
