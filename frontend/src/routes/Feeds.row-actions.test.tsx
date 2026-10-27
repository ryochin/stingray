/**
 * Per-row feed actions wired through the Feeds route: folder move, the
 * translate toggle, the toolbar buttons, delete (single and all), the error
 * filter, blur-to-rename and keyboard expand. Each handler is a one-liner,
 * so a swapped or inverted argument compiles fine and only shows up here.
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
import type { Mock } from "vitest"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { Feed, Folder } from "../api/client"
import { makeFeed, NoopObserver } from "../test-fixtures"
import Feeds from "./Feeds"

vi.mock("../components/Header", () => ({
  default: (): JSX.Element | null => null,
}))

const getFeeds = vi.fn()
const getFolders = vi.fn()
const moveFeedToFolder = vi.fn()
const updateFeedTranslate = vi.fn()
const updateFeedSiteUrl = vi.fn()
const fetchFeed = vi.fn()
const toggleFeed = vi.fn()
const toggleSummarize = vi.fn()
const deleteFeed = vi.fn()
const deleteAllFeeds = vi.fn()
const renameFeed = vi.fn()

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
      getFolders: (): unknown => getFolders(),
      getFeedStats: vi.fn().mockResolvedValue({}),
      moveFeedToFolder: (...args: unknown[]): unknown =>
        moveFeedToFolder(...args),
      updateFeedTranslate: (...args: unknown[]): unknown =>
        updateFeedTranslate(...args),
      updateFeedSiteUrl: (...args: unknown[]): unknown =>
        updateFeedSiteUrl(...args),
      fetchFeed: (...args: unknown[]): unknown => fetchFeed(...args),
      toggleFeed: (...args: unknown[]): unknown => toggleFeed(...args),
      toggleSummarize: (...args: unknown[]): unknown =>
        toggleSummarize(...args),
      deleteFeed: (...args: unknown[]): unknown => deleteFeed(...args),
      deleteAllFeeds: (): unknown => deleteAllFeeds(),
      renameFeed: (...args: unknown[]): unknown => renameFeed(...args),
    },
  }
})

const TECH: Folder = { id: 5, name: "Tech", position: 0 }

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

/** Renders the route with the given feeds and waits for the first row. */
async function renderWith(feeds: Feed[]): Promise<void> {
  getFeeds.mockResolvedValue(feeds)
  renderFeeds()
  await screen.findByText(feeds[0].name)
}

function rowOf(name: string): HTMLElement {
  return screen.getByText(name).closest('[role="listitem"]') as HTMLElement
}

/** The expanded card is the only place "Site URL:" appears. */
function isExpanded(): boolean {
  return screen.queryByText("Site URL:") !== null
}

async function expand(name: string): Promise<void> {
  fireEvent.click(screen.getByText(name))
  await waitFor((): void => expect(isExpanded()).toBe(true))
}

function stubConfirm(answer: boolean): Mock {
  const confirm: Mock = vi.fn((): boolean => answer)
  vi.stubGlobal("confirm", confirm)
  return confirm
}

beforeEach((): void => {
  vi.stubGlobal("IntersectionObserver", NoopObserver)
  vi.stubGlobal("ResizeObserver", NoopObserver)
  for (const mock of [
    getFeeds,
    getFolders,
    moveFeedToFolder,
    updateFeedTranslate,
    updateFeedSiteUrl,
    fetchFeed,
    toggleFeed,
    toggleSummarize,
    deleteFeed,
    deleteAllFeeds,
    renameFeed,
  ]) {
    mock.mockReset()
    mock.mockResolvedValue(undefined)
  }
  getFolders.mockResolvedValue([TECH])
})

afterEach((): void => {
  vi.unstubAllGlobals()
  document.body.innerHTML = ""
})

describe("Feeds row — folder move", (): void => {
  it("moves a feed out of its folder with null, not an empty string", async (): Promise<void> => {
    await renderWith([makeFeed({ id: 1, name: "Alpha", folder_id: 5 })])

    // The "--" option's value is "". The error filter is a combobox too.
    fireEvent.change(within(rowOf("Alpha")).getByRole("combobox"), {
      target: { value: "" },
    })

    await waitFor((): void =>
      expect(moveFeedToFolder).toHaveBeenCalledWith(1, null),
    )
  })

  it("moves a feed into a folder by numeric id", async (): Promise<void> => {
    await renderWith([makeFeed({ id: 1, name: "Alpha", folder_id: null })])

    fireEvent.change(within(rowOf("Alpha")).getByRole("combobox"), {
      target: { value: "5" },
    })

    await waitFor((): void =>
      expect(moveFeedToFolder).toHaveBeenCalledWith(1, 5),
    )
  })
})

describe("Feeds row — translate toggle", (): void => {
  it.each([
    [false, "Translate: OFF", true],
    [true, "Translate: ON", false],
  ])("flips translate=%s", async (current: boolean, label: string, next: boolean): Promise<void> => {
    await renderWith([makeFeed({ id: 1, name: "Alpha", translate: current })])
    await expand("Alpha")

    fireEvent.click(screen.getByRole("button", { name: label }))

    await waitFor((): void =>
      expect(updateFeedTranslate).toHaveBeenCalledWith(1, next),
    )
  })
})

describe("Feeds row — action wiring", (): void => {
  it.each([
    [
      "Fetch now",
      fetchFeed,
      async (row: HTMLElement): Promise<void> => {
        fireEvent.click(within(row).getByTitle("Fetch now"))
      },
    ],
    [
      "ON",
      toggleFeed,
      async (row: HTMLElement): Promise<void> => {
        fireEvent.click(within(row).getByRole("button", { name: "ON" }))
      },
    ],
    [
      "Summarize",
      toggleSummarize,
      async (row: HTMLElement): Promise<void> => {
        fireEvent.click(within(row).getByRole("button", { name: "Summarize" }))
      },
    ],
  ])("%s calls its own API with the feed id", async (_label: string, target: Mock, click: (
    row: HTMLElement,
  ) => Promise<void>): Promise<void> => {
    await renderWith([makeFeed({ id: 1, name: "Alpha" })])

    await click(rowOf("Alpha"))

    await waitFor((): void => expect(target).toHaveBeenCalled())
    // mutationFn is the API function itself: TanStack appends a context
    // argument, so compare only the first one.
    expect(target.mock.calls[0][0]).toBe(1)
    for (const other of [fetchFeed, toggleFeed, toggleSummarize]) {
      if (other !== target) expect(other).not.toHaveBeenCalled()
    }
  })

  it("saves the site URL for the feed", async (): Promise<void> => {
    await renderWith([makeFeed({ id: 1, name: "Alpha" })])
    await expand("Alpha")

    fireEvent.change(screen.getByPlaceholderText("https://example.com/"), {
      target: { value: "https://alpha.test/" },
    })
    fireEvent.click(
      within(rowOf("Alpha")).getByRole("button", { name: "Save" }),
    )

    await waitFor((): void =>
      expect(updateFeedSiteUrl).toHaveBeenCalledWith(1, "https://alpha.test/"),
    )
  })

  it("shows a failed action's error on the page", async (): Promise<void> => {
    toggleFeed.mockRejectedValue(new Error("toggle failed"))
    await renderWith([makeFeed({ id: 1, name: "Alpha" })])

    fireEvent.click(within(rowOf("Alpha")).getByRole("button", { name: "ON" }))

    expect(await screen.findByText("toggle failed")).toHaveAttribute(
      "role",
      "alert",
    )
  })
})

describe("Feeds row — delete", (): void => {
  it("does nothing when the confirmation is declined", async (): Promise<void> => {
    const confirm: Mock = stubConfirm(false)
    await renderWith([makeFeed({ id: 1, name: "Alpha" })])
    await expand("Alpha")

    await act(async (): Promise<void> => {
      fireEvent.click(
        within(rowOf("Alpha")).getByRole("button", { name: "Delete" }),
      )
    })

    expect(confirm).toHaveBeenCalledWith('Delete "Alpha"?')
    expect(deleteFeed).not.toHaveBeenCalled()
  })

  it("deletes the feed once confirmed", async (): Promise<void> => {
    const confirm: Mock = stubConfirm(true)
    await renderWith([makeFeed({ id: 1, name: "Alpha" })])
    await expand("Alpha")

    fireEvent.click(
      within(rowOf("Alpha")).getByRole("button", { name: "Delete" }),
    )

    await waitFor((): void => expect(deleteFeed).toHaveBeenCalled())
    expect(confirm).toHaveBeenCalledWith('Delete "Alpha"?')
    expect(deleteFeed.mock.calls[0][0]).toBe(1)
  })
})

describe("Feeds — Delete All", (): void => {
  const MESSAGE: string =
    "Delete ALL feeds, folders, and articles? This cannot be undone."

  it("does nothing when the confirmation is declined", async (): Promise<void> => {
    const confirm: Mock = stubConfirm(false)
    await renderWith([makeFeed({ id: 1, name: "Alpha" })])

    await act(async (): Promise<void> => {
      fireEvent.click(screen.getByRole("button", { name: "Delete All" }))
    })

    expect(confirm).toHaveBeenCalledWith(MESSAGE)
    expect(deleteAllFeeds).not.toHaveBeenCalled()
  })

  it("refetches feeds and folders after deleting everything", async (): Promise<void> => {
    const confirm: Mock = stubConfirm(true)
    await renderWith([makeFeed({ id: 1, name: "Alpha", folder_id: 5 })])
    await waitFor((): void =>
      expect(screen.queryAllByText("Tech").length).toBeGreaterThan(0),
    )
    const feedCalls: number = getFeeds.mock.calls.length
    const folderCalls: number = getFolders.mock.calls.length
    getFeeds.mockResolvedValue([])
    getFolders.mockResolvedValue([])

    fireEvent.click(screen.getByRole("button", { name: "Delete All" }))

    await waitFor((): void => {
      expect(screen.queryByText("Alpha")).toBeNull()
      expect(screen.queryAllByText("Tech")).toHaveLength(0)
    })
    expect(confirm).toHaveBeenCalledWith(MESSAGE)
    expect(deleteAllFeeds).toHaveBeenCalledTimes(1)
    expect(getFeeds.mock.calls.length).toBeGreaterThan(feedCalls)
    expect(getFolders.mock.calls.length).toBeGreaterThan(folderCalls)
  })

  it("shows the failure", async (): Promise<void> => {
    stubConfirm(true)
    deleteAllFeeds.mockRejectedValue(new Error("nope"))
    await renderWith([makeFeed({ id: 1, name: "Alpha" })])

    fireEvent.click(screen.getByRole("button", { name: "Delete All" }))

    await screen.findByText("nope")
    expect(screen.getByText("Alpha")).toBeInTheDocument()
  })
})

describe("Feeds — error filter", (): void => {
  function chooseErrorFilter(value: "all" | "with" | "without"): void {
    fireEvent.change(screen.getByDisplayValue("All feeds"), {
      target: { value },
    })
  }

  it.each([
    ["with", "Broken", "Flaky"],
    ["without", "Flaky", "Broken"],
  ] as const)("%s errors splits at three consecutive failures", async (value, shown: string, hidden: string): Promise<void> => {
    await renderWith([
      makeFeed({ id: 1, name: "Flaky", consecutive_failures: 2 }),
      makeFeed({ id: 2, name: "Broken", consecutive_failures: 3 }),
    ])

    chooseErrorFilter(value)

    await waitFor((): void => expect(screen.queryByText(hidden)).toBeNull())
    expect(screen.getByText(shown)).toBeInTheDocument()
    expect(screen.getByText("1/2")).toBeInTheDocument()
  })

  it("combines the search and the error filter with AND", async (): Promise<void> => {
    await renderWith([
      makeFeed({ id: 1, name: "Match Broken", consecutive_failures: 3 }),
      makeFeed({ id: 2, name: "Match Healthy", consecutive_failures: 0 }),
      makeFeed({ id: 3, name: "Other Broken", consecutive_failures: 3 }),
    ])

    fireEvent.change(screen.getByPlaceholderText("Filter by name or URL..."), {
      target: { value: "match" },
    })
    chooseErrorFilter("with")

    await waitFor((): void => {
      expect(screen.queryByText("Match Healthy")).toBeNull()
      expect(screen.queryByText("Other Broken")).toBeNull()
    })
    expect(screen.getByText("Match Broken")).toBeInTheDocument()
    expect(screen.getByText("1/3")).toBeInTheDocument()
  })

  it("matches the search against the feed URL", async (): Promise<void> => {
    await renderWith([
      makeFeed({ id: 1, name: "Plain", url: "https://unique-host.test/feed" }),
      makeFeed({ id: 2, name: "Other", url: "https://elsewhere.test/feed" }),
    ])

    fireEvent.change(screen.getByPlaceholderText("Filter by name or URL..."), {
      target: { value: "Unique-Host" },
    })

    await waitFor((): void => expect(screen.queryByText("Other")).toBeNull())
    expect(screen.getByText("Plain")).toBeInTheDocument()
  })
})

describe("Feeds row — rename on blur", (): void => {
  async function startRename(): Promise<HTMLInputElement> {
    await renderWith([makeFeed({ id: 1, name: "Alpha" })])
    fireEvent.click(screen.getByTitle("Rename"))
    return (await screen.findByDisplayValue("Alpha")) as HTMLInputElement
  }

  it("commits a changed name on blur", async (): Promise<void> => {
    const input: HTMLInputElement = await startRename()

    fireEvent.change(input, { target: { value: "Renamed" } })
    fireEvent.blur(input)

    await waitFor((): void =>
      expect(renameFeed).toHaveBeenCalledWith(1, "Renamed"),
    )
  })

  it.each([
    ["the same name", "Alpha"],
    ["a blank name", "   "],
  ])("leaves edit mode without renaming for %s", async (_label: string, value: string): Promise<void> => {
    const input: HTMLInputElement = await startRename()

    fireEvent.change(input, { target: { value } })
    await act(async (): Promise<void> => {
      fireEvent.blur(input)
    })

    expect(renameFeed).not.toHaveBeenCalled()
    expect(input.isConnected).toBe(false)
    expect(screen.getByText("Alpha")).toBeInTheDocument()
  })
})

describe("Feeds row — keyboard expand", (): void => {
  it("expands on Space and collapses on Enter, consuming both keys", async (): Promise<void> => {
    await renderWith([makeFeed({ id: 1, name: "Alpha" })])
    const toggle: HTMLElement = screen
      .getByText("Alpha")
      .closest('[role="button"]') as HTMLElement

    // fireEvent returns false when the handler called preventDefault.
    expect(fireEvent.keyDown(toggle, { key: " " })).toBe(false)
    await waitFor((): void => expect(isExpanded()).toBe(true))

    expect(fireEvent.keyDown(toggle, { key: "Enter" })).toBe(false)
    await waitFor((): void => expect(isExpanded()).toBe(false))
  })
})
