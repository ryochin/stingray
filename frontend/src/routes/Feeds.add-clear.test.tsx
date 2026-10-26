/**
 * Guards the AddFeedForm input-clearing behaviour: the URL/Name inputs are
 * cleared only after a *successful* add, and kept when the add fails (so the
 * user can fix the value or pick a resolved feed candidate). The success path
 * runs through react-query's `mutateAsync`, so this exercises the real
 * mutation wiring rather than the form in isolation.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import type { JSX } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { Folder } from "../api/client"
import { ApiError } from "../api/client"
import Feeds from "./Feeds"

// The route chrome is irrelevant here and would drag in refresh polling.
vi.mock("../components/Header", () => ({
  default: (): JSX.Element | null => null,
}))

const createFeed = vi.fn()
const getFolders = vi.fn()

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
      getFolders: (...args: unknown[]): unknown => getFolders(...args),
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
  getFolders.mockReset()
  getFolders.mockResolvedValue([])
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

  it("keeps values typed for the next feed while the add is in flight", async (): Promise<void> => {
    let resolve: (value: unknown) => void = (): void => {}
    createFeed.mockReturnValue(
      new Promise<unknown>((r): void => {
        resolve = r
      }),
    )
    renderFeeds()

    fireEvent.change(getUrlInput(), {
      target: { value: "https://a.test/feed" },
    })
    fireEvent.change(getNameInput(), { target: { value: "A" } })
    fireEvent.submit(getUrlInput().closest("form") as HTMLFormElement)
    await waitFor((): void => expect(createFeed).toHaveBeenCalledTimes(1))

    fireEvent.change(getUrlInput(), {
      target: { value: "https://b.test/feed" },
    })
    fireEvent.change(getNameInput(), { target: { value: "B" } })
    await act(async (): Promise<void> => {
      resolve({ id: 1, name: "A", url: "https://a.test/feed" })
    })

    expect(getUrlInput().value).toBe("https://b.test/feed")
    expect(getNameInput().value).toBe("B")
  })
})

const TECH: Folder = { id: 5, name: "Tech", position: 0 }

const CANDIDATE_ERROR: ApiError = new ApiError("html page", 422, {
  detail: {
    candidates: [
      {
        href: "https://site.test/rss",
        title: "Site RSS",
        type: "application/rss+xml",
      },
    ],
  },
})

function getFeedForm(): HTMLFormElement {
  return getUrlInput().closest("form") as HTMLFormElement
}

describe("AddFeedForm folder", (): void => {
  it("sends the chosen folder as a number and omits it for --", async (): Promise<void> => {
    getFolders.mockResolvedValue([TECH])
    createFeed.mockResolvedValue({ id: 1, name: "X", url: "https://x.test" })
    renderFeeds()
    // "Tech" also appears in the FolderManager, so stay inside the form.
    const folderSelect: HTMLSelectElement = within(getFeedForm()).getByRole(
      "combobox",
    ) as HTMLSelectElement
    await within(folderSelect).findByRole("option", { name: "Tech" })

    fireEvent.change(getUrlInput(), { target: { value: "https://x.test/1" } })
    fireEvent.change(folderSelect, { target: { value: "5" } })
    fireEvent.submit(getFeedForm())
    await waitFor((): void => expect(createFeed).toHaveBeenCalledTimes(1))
    // mutationFn is api.createFeed itself, so TanStack appends its context.
    expect(createFeed.mock.calls[0][0]).toEqual({
      name: "",
      url: "https://x.test/1",
      folder_id: 5,
    })

    await waitFor((): void => expect(getUrlInput().value).toBe(""))
    fireEvent.change(getUrlInput(), { target: { value: "https://x.test/2" } })
    fireEvent.change(folderSelect, { target: { value: "" } })
    fireEvent.submit(getFeedForm())
    await waitFor((): void => expect(createFeed).toHaveBeenCalledTimes(2))
    expect(createFeed.mock.calls[1][0]).toEqual({
      name: "",
      url: "https://x.test/2",
    })
    // toEqual ignores undefined keys, so check the key itself is absent.
    expect(createFeed.mock.calls[1][0]).not.toHaveProperty("folder_id")
  })
})

describe("AddFeedForm candidates", (): void => {
  async function showCandidates(): Promise<void> {
    renderFeeds()
    fireEvent.change(getUrlInput(), { target: { value: "https://site.test" } })
    fireEvent.submit(getFeedForm())
    await screen.findByRole("button", { name: "Use" })
  }

  it("adds the picked candidate by its href and clears the form", async (): Promise<void> => {
    createFeed.mockRejectedValueOnce(CANDIDATE_ERROR)
    createFeed.mockResolvedValueOnce({
      id: 1,
      name: "Site RSS",
      url: "https://site.test/rss",
    })
    await showCandidates()

    fireEvent.click(screen.getByRole("button", { name: "Use" }))

    await waitFor((): void => expect(createFeed).toHaveBeenCalledTimes(2))
    expect(createFeed.mock.calls[1][0]).toEqual({
      url: "https://site.test/rss",
      name: "",
    })
    await waitFor((): void => expect(getUrlInput().value).toBe(""))
    expect(screen.queryByRole("button", { name: "Use" })).toBeNull()
  })

  it("adds the picked candidate into the chosen folder", async (): Promise<void> => {
    getFolders.mockResolvedValue([TECH])
    createFeed.mockRejectedValueOnce(CANDIDATE_ERROR)
    createFeed.mockResolvedValueOnce({
      id: 1,
      name: "Site RSS",
      url: "https://site.test/rss",
    })
    renderFeeds()
    const folderSelect: HTMLSelectElement = within(getFeedForm()).getByRole(
      "combobox",
    ) as HTMLSelectElement
    await within(folderSelect).findByRole("option", { name: "Tech" })
    fireEvent.change(folderSelect, { target: { value: "5" } })
    fireEvent.change(getUrlInput(), { target: { value: "https://site.test" } })
    fireEvent.submit(getFeedForm())

    fireEvent.click(await screen.findByRole("button", { name: "Use" }))

    await waitFor((): void => expect(createFeed).toHaveBeenCalledTimes(2))
    expect(createFeed.mock.calls[1][0]).toEqual({
      url: "https://site.test/rss",
      name: "",
      folder_id: 5,
    })
    // The folder stays selected for the next add, as after a main submit.
    await waitFor((): void => expect(getUrlInput().value).toBe(""))
    expect(folderSelect.value).toBe("5")
  })

  it("keeps values typed while the picked candidate is being added", async (): Promise<void> => {
    let resolve: (value: unknown) => void = (): void => {}
    createFeed.mockRejectedValueOnce(CANDIDATE_ERROR)
    createFeed.mockReturnValueOnce(
      new Promise<unknown>((r): void => {
        resolve = r
      }),
    )
    await showCandidates()

    fireEvent.click(screen.getByRole("button", { name: "Use" }))
    await waitFor((): void => expect(createFeed).toHaveBeenCalledTimes(2))
    expect(getUrlInput().value).toBe("https://site.test/rss")

    fireEvent.change(getUrlInput(), { target: { value: "https://next.test" } })
    fireEvent.change(getNameInput(), { target: { value: "Next" } })
    await act(async (): Promise<void> => {
      resolve({ id: 1, name: "Site RSS", url: "https://site.test/rss" })
    })

    expect(getUrlInput().value).toBe("https://next.test")
    expect(getNameInput().value).toBe("Next")
  })
})
