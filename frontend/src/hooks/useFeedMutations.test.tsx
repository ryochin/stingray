/**
 * The optimistic reorder and the per-feed fetching indicator — the only two
 * mutations here that carry logic rather than "call the API, then invalidate".
 * Both fail silently when broken: a wrong order still renders, and a stuck
 * spinner looks like a slow backend.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, cleanup, renderHook, waitFor } from "@testing-library/react"
import type { JSX, ReactNode } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { Feed } from "../api/client"
import { api } from "../api/client"
import { makeFeed } from "../test-fixtures"
import { useFeedMutations } from "./useFeedMutations"

// Two folders on purpose: a reorder inside folder 10 must leave the folder-20
// feed sitting at its original index.
const f1: Feed = makeFeed({ id: 1, name: "One", folder_id: 10 })
const other: Feed = makeFeed({ id: 4, name: "Other", folder_id: 20 })
const f2: Feed = makeFeed({ id: 2, name: "Two", folder_id: 10 })
const f3: Feed = makeFeed({ id: 3, name: "Three", folder_id: 10 })
const CACHED: Feed[] = [f1, other, f2, f3]

let queryClient: QueryClient
let onError: (message: string) => void

function wrapper({ children }: { children: ReactNode }): JSX.Element {
  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

function setup() {
  queryClient.setQueryData<Feed[]>(["feeds"], CACHED)
  return renderHook(() => useFeedMutations({ onError }), { wrapper })
}

function cachedIds(): number[] {
  return (queryClient.getQueryData<Feed[]>(["feeds"]) ?? []).map(
    (f: Feed): number => f.id,
  )
}

beforeEach((): void => {
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  onError = vi.fn()
})

afterEach((): void => {
  cleanup()
  vi.restoreAllMocks()
})

describe("useFeedMutations — optimistic reorder", (): void => {
  it("applies the new order before the server answers, leaving other folders untouched", async (): Promise<void> => {
    // Never resolves: the cache must already be reordered while in flight.
    vi.spyOn(api, "reorderFeeds").mockReturnValue(new Promise(() => {}))
    const view = setup()

    act((): void => view.result.current.reorderFeeds.mutate([2, 1, 3]))

    await waitFor((): void => expect(cachedIds()).toEqual([2, 4, 1, 3]))
  })

  it("rolls back to the pre-mutation snapshot and reports the failure", async (): Promise<void> => {
    vi.spyOn(api, "reorderFeeds").mockRejectedValue(new Error("nope"))
    const view = setup()

    act((): void => view.result.current.reorderFeeds.mutate([2, 1, 3]))

    await waitFor((): void => expect(onError).toHaveBeenCalledWith("nope"))
    expect(cachedIds()).toEqual([1, 4, 2, 3])
  })
})

describe("useFeedMutations — fetch indicator", (): void => {
  beforeEach((): void => {
    // setTimeout/clearTimeout only: happy-dom's own scheduling conflicts with
    // the default `toFake` list and hangs the cleanup hook.
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] })
  })

  afterEach((): void => {
    vi.useRealTimers()
  })

  it("marks the feed as fetching and clears it 5s after the request settles", async (): Promise<void> => {
    vi.spyOn(api, "fetchFeed").mockResolvedValue({ message: "queued" })
    const view = setup()

    // `waitFor` schedules its own timers, so it never settles while they are
    // faked — flush the microtask queue through `act` instead.
    await act(async (): Promise<void> => {
      view.result.current.fetchFeed.mutate(1)
    })
    expect(view.result.current.fetchingFeeds.has(1)).toBe(true)

    await act(async (): Promise<void> => {
      vi.advanceTimersByTime(4999)
    })
    expect(view.result.current.fetchingFeeds.has(1)).toBe(true)

    await act(async (): Promise<void> => {
      vi.advanceTimersByTime(1)
    })
    expect(view.result.current.fetchingFeeds.has(1)).toBe(false)
  })

  it("drops the pending clear when unmounted after the request settles", async (): Promise<void> => {
    vi.spyOn(api, "fetchFeed").mockResolvedValue({ message: "queued" })
    const view = setup()
    await act(async (): Promise<void> => {
      view.result.current.fetchFeed.mutate(1)
    })
    const invalidate = vi.spyOn(queryClient, "invalidateQueries")

    view.unmount()
    await act(async (): Promise<void> => {
      vi.advanceTimersByTime(5000)
    })

    expect(invalidate).not.toHaveBeenCalled()
  })

  it.each([
    ["succeeds", true],
    ["fails", false],
  ])("schedules no clear when the request %s after unmount", async (_label: string, ok: boolean): Promise<void> => {
    let settle: (() => void) | undefined
    vi.spyOn(api, "fetchFeed").mockReturnValue(
      new Promise<{ message: string }>((resolve, reject): void => {
        settle = (): void =>
          ok ? resolve({ message: "queued" }) : reject(new Error("nope"))
      }),
    )
    const view = setup()
    await act(async (): Promise<void> => {
      view.result.current.fetchFeed.mutate(1)
    })
    const invalidate = vi.spyOn(queryClient, "invalidateQueries")

    view.unmount()
    await act(async (): Promise<void> => {
      settle?.()
    })
    await act(async (): Promise<void> => {
      vi.advanceTimersByTime(5000)
    })

    expect(invalidate).not.toHaveBeenCalled()
  })
})
