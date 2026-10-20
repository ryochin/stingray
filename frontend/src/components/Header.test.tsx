/**
 * Guards the Header's status display, the Refresh button and the status
 * polling cadence. The status failure marker lives in
 * `Header.status-error.test.tsx`.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, cleanup, render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import type { Mock, MockInstance } from "vitest"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { RefreshStatus } from "../api/client"
import { formatTime } from "../utils/date"
import Header from "./Header"

const getStatus: Mock = vi.fn()
const refresh: Mock = vi.fn()

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>()
  return {
    ...actual,
    api: {
      getStatus: (): unknown => getStatus(),
      refresh: (): unknown => refresh(),
    },
  }
})

function status(overrides: Partial<RefreshStatus> = {}): RefreshStatus {
  return {
    running: false,
    last_started_at: null,
    last_finished_at: null,
    last_status: null,
    last_new_count: null,
    last_error: null,
    llm_enabled: true,
    llm_available: true,
    llm_error: null,
    ...overrides,
  }
}

let client: QueryClient

function renderHeader(): void {
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Header />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function refreshButton(): HTMLButtonElement {
  return screen.getByRole("button", { name: /Refresh/ }) as HTMLButtonElement
}

beforeEach((): void => {
  getStatus.mockReset()
  refresh.mockReset()
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
})

afterEach((): void => {
  cleanup()
  client.clear()
})

describe("Header LLM status", (): void => {
  it("shows LLM off when disabled in config", async (): Promise<void> => {
    getStatus.mockResolvedValue(status({ llm_enabled: false }))
    renderHeader()
    await screen.findByText("– LLM off")
  })

  it("shows LLM online when available", async (): Promise<void> => {
    getStatus.mockResolvedValue(status())
    renderHeader()
    const online: HTMLElement = await screen.findByTitle("LLM online")
    expect(online.textContent).toBe("LLM")
  })

  it.each([
    [
      "with the error",
      "connection refused",
      "LLM unavailable: connection refused",
    ],
    ["without an error", null, "LLM unavailable"],
  ])("shows LLM offline %s in the title", async (_: string, llmError:
    | string
    | null, title: string): Promise<void> => {
    getStatus.mockResolvedValue(
      status({ llm_available: false, llm_error: llmError }),
    )
    renderHeader()
    const offline: HTMLElement = await screen.findByText("⚠ LLM offline")
    expect(offline.title).toBe(title)
  })
})

describe("Header fetch status", (): void => {
  const finishedAt: string = "2026-10-20T09:30:00Z"

  it("shows Fetching and hides the last run while running", async (): Promise<void> => {
    getStatus.mockResolvedValue(
      status({
        running: true,
        last_finished_at: finishedAt,
        last_new_count: 3,
      }),
    )
    renderHeader()
    await screen.findByText("Fetching...")
    expect(
      screen.queryByText(formatTime(finishedAt), { exact: false }),
    ).toBeNull()
    expect(refreshButton().disabled).toBe(true)
  })

  it.each([
    [5, `${formatTime(finishedAt)} / 5 new`],
    [0, `${formatTime(finishedAt)} / 0 new`],
    [null, formatTime(finishedAt)],
  ])("shows the last run with new count %s", async (count:
    | number
    | null, text: string): Promise<void> => {
    getStatus.mockResolvedValue(
      status({ last_finished_at: finishedAt, last_new_count: count }),
    )
    renderHeader()
    const lastRun: HTMLElement = await screen.findByText(
      formatTime(finishedAt),
      {
        exact: false,
      },
    )
    expect(lastRun.textContent).toBe(text)
    expect(screen.queryByText("Fetching...")).toBeNull()
  })
})

describe("Header Refresh button", (): void => {
  it("starts a refresh and re-reads the status on success", async (): Promise<void> => {
    getStatus.mockResolvedValue(status())
    refresh.mockResolvedValue(undefined)
    renderHeader()
    await screen.findByTitle("LLM online")
    const before: number = getStatus.mock.calls.length

    await act(async (): Promise<void> => {
      refreshButton().click()
    })

    expect(refresh).toHaveBeenCalledTimes(1)
    expect(getStatus.mock.calls.length).toBeGreaterThan(before)
  })

  it("is disabled while the refresh request is pending", async (): Promise<void> => {
    getStatus.mockResolvedValue(status())
    let resolve: () => void = (): void => {}
    refresh.mockReturnValue(
      new Promise<void>((r: () => void): void => {
        resolve = r
      }),
    )
    renderHeader()
    await screen.findByTitle("LLM online")
    expect(refreshButton().disabled).toBe(false)

    await act(async (): Promise<void> => {
      refreshButton().click()
    })
    // Mutation state reaches React through a setTimeout(0) notification.
    await waitFor((): void => expect(refreshButton().disabled).toBe(true))

    await act(async (): Promise<void> => {
      resolve()
    })
    await waitFor((): void => expect(refreshButton().disabled).toBe(false))
  })
})

describe("Header status polling", (): void => {
  beforeEach((): void => {
    vi.useFakeTimers({
      toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval"],
    })
  })

  afterEach((): void => {
    cleanup()
    client.clear()
    vi.useRealTimers()
  })

  async function advance(ms: number): Promise<void> {
    await act(async (): Promise<void> => {
      await vi.advanceTimersByTimeAsync(ms)
    })
  }

  it.each([
    ["idle", false, 30_000],
    ["running", true, 2_000],
  ])("polls every interval while %s", async (_: string, running: boolean, interval: number): Promise<void> => {
    getStatus.mockResolvedValue(status({ running }))
    renderHeader()
    // Let the initial fetch and its notifications settle.
    await advance(0)
    expect(getStatus).toHaveBeenCalledTimes(1)

    await advance(interval - 1)
    expect(getStatus).toHaveBeenCalledTimes(1)
    await advance(1)
    expect(getStatus).toHaveBeenCalledTimes(2)
  })

  it("invalidates list caches when a running fetch finishes", async (): Promise<void> => {
    getStatus.mockResolvedValue(status({ running: true }))
    const invalidate: MockInstance = vi.spyOn(client, "invalidateQueries")
    renderHeader()
    await advance(0)
    invalidate.mockClear()

    getStatus.mockResolvedValue(status({ running: false }))
    await advance(2_000)
    // The refetch's result notification is a zero-delay timer queued while
    // the clock is advancing; it only fires once the clock moves again.
    await advance(1)
    expect(screen.queryByText("Fetching...")).toBeNull()

    for (const key of ["articles", "feeds", "folders", "feed-stats"]) {
      expect(invalidate).toHaveBeenCalledWith({ queryKey: [key] })
    }
  })
})
