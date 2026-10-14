/**
 * Guards that marking an article unread puts it back into the unread count.
 * A session read is subtracted from the stats-derived count until the DB
 * catches up; if the URL stayed in the session set after `m` reverted it,
 * the refetched (unread again) article kept being subtracted for the rest of
 * the selection. The document title carries the same total as the sidebar.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import type { JSX } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { Article, Feed, FeedStats, Selection } from "../api/client"
import { api } from "../api/client"
import Articles from "./Articles"

vi.mock("../components/Header", () => ({
  default: (): JSX.Element | null => null,
}))
vi.mock("../components/Sidebar", () => ({
  default: (): JSX.Element | null => null,
}))

// Mutable server state shared by the mocked API below.
const server: { readUrls: Set<string> } = { readUrls: new Set<string>() }

const FEED: Feed = {
  id: 1,
  name: "Feed",
  url: "https://example.com/feed",
  site_url: null,
  translate: false,
  summarize: false,
  enabled: true,
  folder_id: null,
  position: 0,
  last_fetched_at: null,
  consecutive_failures: 0,
  last_error: null,
  health: "ok",
  extraction_rules: null,
  created_at: "2026-10-01T00:00:00Z",
}

const ARTICLE_URLS: readonly string[] = [
  "https://example.com/a",
  "https://example.com/b",
]

function articleFor(url: string): Article {
  return {
    url,
    feed_id: FEED.id,
    title: url,
    title_translated: null,
    source: "Feed",
    published: new Date().toISOString(),
    content_snippet: null,
    summary: null,
    content_html: null,
    content_translated: null,
    read_at: server.readUrls.has(url) ? new Date().toISOString() : null,
  }
}

function stats(): Record<string, FeedStats> {
  const unread: number = ARTICLE_URLS.filter(
    (url: string): boolean => !server.readUrls.has(url),
  ).length
  return {
    [FEED.id]: {
      article_count: ARTICLE_URLS.length,
      unread_count: unread,
      latest_published: null,
      oldest_published: null,
    },
  }
}

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
      getArticles: vi.fn(),
      getFeeds: vi.fn(),
      getFolders: vi.fn().mockResolvedValue([]),
      getFeedStats: vi.fn(),
      markRead: vi.fn(),
      markUnread: vi.fn(),
      markAllRead: vi.fn(),
      markAllUnread: vi.fn(),
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

function press(key: string): void {
  fireEvent.keyDown(window, { key })
}

async function expectTitle(title: string): Promise<void> {
  await waitFor((): void => expect(document.title).toBe(title), {
    timeout: 2000,
  })
}

beforeEach((): void => {
  vi.clearAllMocks()
  vi.stubGlobal("IntersectionObserver", NoopObserver)
  vi.stubGlobal("ResizeObserver", NoopObserver)
  vi.stubGlobal("open", vi.fn())
  sessionStorage.clear()
  server.readUrls = new Set<string>()
  vi.mocked(api.getArticles).mockImplementation(
    async (): Promise<Article[]> => ARTICLE_URLS.map(articleFor),
  )
  vi.mocked(api.getFeeds).mockResolvedValue([FEED])
  vi.mocked(api.getFeedStats).mockImplementation(
    async (): Promise<Record<string, FeedStats>> => stats(),
  )
  vi.mocked(api.markRead).mockImplementation(
    async (urls: string[]): Promise<void> => {
      for (const url of urls) server.readUrls.add(url)
    },
  )
  vi.mocked(api.markUnread).mockImplementation(
    async (urls: string[]): Promise<void> => {
      for (const url of urls) server.readUrls.delete(url)
    },
  )
  vi.mocked(api.markAllUnread).mockImplementation(
    async (_scope: Selection): Promise<{ unmarked: number }> => {
      const unmarked: number = server.readUrls.size
      server.readUrls.clear()
      return { unmarked }
    },
  )
})

afterEach((): void => {
  vi.unstubAllGlobals()
  document.body.innerHTML = ""
})

function renderArticles(): QueryClient {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <Articles />
    </QueryClientProvider>,
  )
  return client
}

function cachedReadAt(client: QueryClient, url: string): string | null {
  const [, articles] = client.getQueriesData<Article[]>({
    queryKey: ["articles"],
  })[0]
  return articles?.find((a: Article): boolean => a.url === url)?.read_at ?? null
}

// Reads the first article and waits until the refetched list shows it as
// read, so a following `m` takes the mark-unread branch.
async function renderAndReadFirstArticle(): Promise<QueryClient> {
  const client: QueryClient = renderArticles()
  await expectTitle("Stingray (2)")

  // The list auto-focuses the first article; opening it marks it read
  // in-session.
  act((): void => press("v"))
  await expectTitle("Stingray (1)")
  await waitFor(
    (): void => expect(cachedReadAt(client, ARTICLE_URLS[0])).not.toBeNull(),
    { timeout: 2000 },
  )
  await expectTitle("Stingray (1)")
  return client
}

function openMarkAllUnread(): void {
  fireEvent.click(screen.getByRole("button", { name: "Mark all as read ▾" }))
  fireEvent.click(screen.getByRole("button", { name: "Mark all as unread" }))
}

describe("Articles unread count after marking unread", (): void => {
  it("adds a single article marked unread back to the count", async (): Promise<void> => {
    await renderAndReadFirstArticle()

    act((): void => press("m"))
    await waitFor((): void => expect(api.markUnread).toHaveBeenCalled())
    await expectTitle("Stingray (2)")
  })

  it("adds articles back to the count after mark-all-unread", async (): Promise<void> => {
    await renderAndReadFirstArticle()

    openMarkAllUnread()
    await waitFor((): void => expect(api.markAllUnread).toHaveBeenCalled())
    await expectTitle("Stingray (2)")
  })

  it("sends a read of the same article made while mark-unread is in flight", async (): Promise<void> => {
    const client: QueryClient = await renderAndReadFirstArticle()
    let release: () => void = (): void => {}
    vi.mocked(api.markUnread).mockImplementationOnce(
      (urls: string[]): Promise<void> =>
        new Promise<void>((resolve: () => void): void => {
          release = (): void => {
            for (const url of urls) server.readUrls.delete(url)
            resolve()
          }
        }),
    )

    act((): void => press("m"))
    // Shown as unread before the server answers.
    await waitFor((): void =>
      expect(cachedReadAt(client, ARTICLE_URLS[0])).toBeNull(),
    )
    // Read it again, then let mark-unread settle while that read is still
    // queued: nothing after the response may drop it.
    act((): void => press("v"))
    act((): void => release())
    await waitFor((): void => expect(api.markRead).toHaveBeenCalledTimes(2), {
      timeout: 2000,
    })
    expect(api.markRead).toHaveBeenLastCalledWith([ARTICLE_URLS[0]])
    expect(server.readUrls.has(ARTICLE_URLS[0])).toBe(true)
    await expectTitle("Stingray (1)")
  })

  it("drops a queued read when mark-all-unread starts before it is sent", async (): Promise<void> => {
    renderArticles()
    await expectTitle("Stingray (2)")

    // No await in between, so the 500ms debounce can't fire before
    // mark-all-unread starts.
    act((): void => press("v"))
    openMarkAllUnread()
    await waitFor((): void => expect(api.markAllUnread).toHaveBeenCalled())
    await expectTitle("Stingray (2)")

    // Outlast the 500ms debounce the queued read was waiting on.
    await new Promise<void>((resolve: () => void): void => {
      setTimeout(resolve, 800)
    })
    expect(api.markRead).not.toHaveBeenCalled()
    await expectTitle("Stingray (2)")
  })
})
