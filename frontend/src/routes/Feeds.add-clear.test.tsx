/**
 * Guards the AddFeedForm input-clearing behaviour: the URL/Name inputs are
 * cleared only after a *successful* add, and kept when the add fails (so the
 * user can fix the value or pick a resolved feed candidate). The success path
 * runs through react-query's `mutateAsync`, so this exercises the real
 * mutation wiring rather than the form in isolation.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import type { JSX } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { ApiError } from "../api/client"
import Feeds from "./Feeds"

// The route chrome is irrelevant here and would drag in refresh polling.
vi.mock("../components/Header", () => ({
  default: (): JSX.Element | null => null,
}))

const createFeed = vi.fn()

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
      getFeeds: vi.fn().mockResolvedValue([]),
      getFolders: vi.fn().mockResolvedValue([]),
      getFeedStats: vi.fn().mockResolvedValue({}),
      createFeed: (...args: unknown[]): unknown => createFeed(...args),
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

function getUrlInput(): HTMLInputElement {
  return screen.getByPlaceholderText(
    "https://example.com/feed",
  ) as HTMLInputElement
}

function getNameInput(): HTMLInputElement {
  return screen.getByPlaceholderText("Auto-detect") as HTMLInputElement
}

beforeEach((): void => {
  vi.stubGlobal("IntersectionObserver", NoopObserver)
  vi.stubGlobal("ResizeObserver", NoopObserver)
  createFeed.mockReset()
})

afterEach((): void => {
  vi.unstubAllGlobals()
  document.body.innerHTML = ""
})

describe("AddFeedForm input clearing", (): void => {
  it("clears the URL and Name inputs after a successful add", async (): Promise<void> => {
    createFeed.mockResolvedValue({
      id: 1,
      name: "Example",
      url: "https://x.test/feed",
    })
    renderFeeds()

    const url: HTMLInputElement = getUrlInput()
    fireEvent.change(url, { target: { value: "https://x.test/feed" } })
    fireEvent.change(getNameInput(), { target: { value: "My Feed" } })
    // Submit the feed form directly: a "Add" button label is shared with the
    // folder form, so target this form to stay unambiguous.
    fireEvent.submit(url.closest("form") as HTMLFormElement)

    await waitFor((): void => expect(createFeed).toHaveBeenCalledTimes(1))
    await waitFor((): void => expect(getUrlInput().value).toBe(""))
    expect(getNameInput().value).toBe("")
  })

  it("keeps the URL and Name inputs when the add fails", async (): Promise<void> => {
    createFeed.mockRejectedValue(new Error("boom"))
    renderFeeds()

    const url: HTMLInputElement = getUrlInput()
    fireEvent.change(url, { target: { value: "https://broken.test/feed" } })
    fireEvent.change(getNameInput(), { target: { value: "Broken" } })
    fireEvent.submit(url.closest("form") as HTMLFormElement)

    await waitFor((): void => expect(createFeed).toHaveBeenCalledTimes(1))
    // The values must survive the failed attempt; give the rejected promise a
    // chance to settle before asserting they were not cleared.
    await Promise.resolve()
    expect(getUrlInput().value).toBe("https://broken.test/feed")
    expect(getNameInput().value).toBe("Broken")
  })

  it("keeps the inputs and shows candidates when the URL resolves to feed candidates", async (): Promise<void> => {
    // The backend answers a site URL that is an HTML page with 422 + candidate
    // feeds. The form must keep the typed URL so the user can pick a candidate.
    createFeed.mockRejectedValue(
      new ApiError("html page", 422, {
        detail: {
          candidates: [
            {
              href: "https://site.test/rss",
              title: "RSS",
              type: "application/rss+xml",
            },
          ],
        },
      }),
    )
    renderFeeds()

    const url: HTMLInputElement = getUrlInput()
    fireEvent.change(url, { target: { value: "https://site.test" } })
    fireEvent.submit(url.closest("form") as HTMLFormElement)

    await waitFor((): void => expect(createFeed).toHaveBeenCalledTimes(1))
    // The candidate list surfaces (a "Use" button per candidate) and the typed
    // URL is preserved for context.
    expect(await screen.findByRole("button", { name: "Use" })).toBeTruthy()
    expect(getUrlInput().value).toBe("https://site.test")
  })
})
