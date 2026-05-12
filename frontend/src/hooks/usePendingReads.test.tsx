import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, cleanup, renderHook } from "@testing-library/react"
import type { JSX, ReactNode } from "react"
import type { Mock } from "vitest"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { api } from "../api/client"
import { usePendingReads } from "./usePendingReads"

function wrapper({ children }: { children: ReactNode }): JSX.Element {
  // A fresh client per test isolates `invalidateQueries` observations.
  const queryClient: QueryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

let markReadSpy: Mock

beforeEach((): void => {
  // Limit fake timers to setTimeout/clearTimeout only — happy-dom's
  // internal scheduling conflicts with the default `toFake` list and
  // makes the global cleanup hook hang.
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] })
  // Resolve immediately so the flush's chained `.then` runs deterministically
  // under fake timers (no real microtask scheduling involved beyond what
  // `await flushPromises` covers).
  markReadSpy = vi.fn(
    async (_urls: string[]): Promise<void> => undefined,
  ) as unknown as Mock
  vi.spyOn(api, "markRead").mockImplementation(markReadSpy)
})

afterEach((): void => {
  // Unmount while the markRead spy is still active — the hook's unmount
  // effect flushes pending reads, and we don't want that to hit the real
  // fetch through a restored mock after the global cleanup runs.
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe("usePendingReads — scheduleRead batches via debounce", (): void => {
  it("flushes a single batch after the debounce window with all queued URLs", (): void => {
    const { result } = renderHook(
      (): ReturnType<typeof usePendingReads> => usePendingReads(),
      { wrapper },
    )

    act((): void => {
      result.current.scheduleRead("a")
      result.current.scheduleRead("b")
      result.current.scheduleRead("c")
    })

    // Before the debounce elapses, no request fires.
    expect(markReadSpy).not.toHaveBeenCalled()

    act((): void => {
      vi.advanceTimersByTime(500)
    })

    expect(markReadSpy).toHaveBeenCalledTimes(1)
    expect(markReadSpy.mock.calls[0][0].sort()).toEqual(["a", "b", "c"])
  })

  it("resets the timer on every scheduleRead — keep adding within the window and only one flush fires", (): void => {
    const { result } = renderHook(
      (): ReturnType<typeof usePendingReads> => usePendingReads(),
      { wrapper },
    )

    act((): void => {
      result.current.scheduleRead("a")
    })
    act((): void => {
      vi.advanceTimersByTime(400)
    })
    // Still within window — schedule another, resets the timer.
    act((): void => {
      result.current.scheduleRead("b")
    })
    act((): void => {
      vi.advanceTimersByTime(400)
    })
    expect(markReadSpy).not.toHaveBeenCalled()

    // Now finish the window from the last scheduleRead.
    act((): void => {
      vi.advanceTimersByTime(100)
    })
    expect(markReadSpy).toHaveBeenCalledTimes(1)
    expect(markReadSpy.mock.calls[0][0].sort()).toEqual(["a", "b"])
  })

  it("does not re-queue or re-tick localReadCount for a URL already in the session set", (): void => {
    const { result } = renderHook(
      (): ReturnType<typeof usePendingReads> => usePendingReads(),
      { wrapper },
    )

    act((): void => {
      result.current.scheduleRead("a")
      result.current.scheduleRead("a") // duplicate
      result.current.scheduleRead("b")
    })

    // Two unique URLs → counter advanced exactly twice.
    expect(result.current.localReadCount).toBe(2)

    act((): void => {
      vi.advanceTimersByTime(500)
    })

    expect(markReadSpy).toHaveBeenCalledTimes(1)
    expect(markReadSpy.mock.calls[0][0].sort()).toEqual(["a", "b"])
  })
})

describe("usePendingReads — flushReads (manual)", (): void => {
  it("flushes immediately when called and is a no-op on an empty queue", (): void => {
    const { result } = renderHook(
      (): ReturnType<typeof usePendingReads> => usePendingReads(),
      { wrapper },
    )

    // No-op on empty queue.
    act((): void => {
      result.current.flushReads()
    })
    expect(markReadSpy).not.toHaveBeenCalled()

    act((): void => {
      result.current.scheduleRead("a")
      result.current.flushReads()
    })

    expect(markReadSpy).toHaveBeenCalledTimes(1)
    expect(markReadSpy.mock.calls[0][0]).toEqual(["a"])
  })

  it("flushes the queue on unmount so a tab close mid-debounce does not drop reads", (): void => {
    const { result, unmount } = renderHook(
      (): ReturnType<typeof usePendingReads> => usePendingReads(),
      { wrapper },
    )

    act((): void => {
      result.current.scheduleRead("a")
    })
    expect(markReadSpy).not.toHaveBeenCalled()

    unmount()

    expect(markReadSpy).toHaveBeenCalledTimes(1)
    expect(markReadSpy.mock.calls[0][0]).toEqual(["a"])
  })
})

describe("usePendingReads — session state", (): void => {
  it("hasSessionRead reflects URLs accepted via scheduleRead and is stable across renders", (): void => {
    const { result, rerender } = renderHook(
      (): ReturnType<typeof usePendingReads> => usePendingReads(),
      { wrapper },
    )

    expect(result.current.hasSessionRead("a")).toBe(false)

    const probeBefore = result.current.hasSessionRead
    act((): void => {
      result.current.scheduleRead("a")
    })

    expect(result.current.hasSessionRead("a")).toBe(true)
    expect(result.current.hasSessionRead("missing")).toBe(false)

    // Identity stability matters — downstream memos depend on it.
    rerender()
    expect(result.current.hasSessionRead).toBe(probeBefore)
  })

  it("resetSessionReads clears the set and resets localReadCount to 0", (): void => {
    const { result } = renderHook(
      (): ReturnType<typeof usePendingReads> => usePendingReads(),
      { wrapper },
    )

    act((): void => {
      result.current.scheduleRead("a")
      result.current.scheduleRead("b")
    })
    expect(result.current.localReadCount).toBe(2)
    expect(result.current.hasSessionRead("a")).toBe(true)

    act((): void => {
      result.current.resetSessionReads()
    })

    expect(result.current.localReadCount).toBe(0)
    expect(result.current.hasSessionRead("a")).toBe(false)
    expect(result.current.hasSessionRead("b")).toBe(false)
  })

  it("sessionReadUrls is a mutable ref carrying the underlying Set so derivation helpers can consume it directly", (): void => {
    const { result } = renderHook(
      (): ReturnType<typeof usePendingReads> => usePendingReads(),
      { wrapper },
    )

    act((): void => {
      result.current.scheduleRead("a")
    })

    expect(result.current.sessionReadUrls.current).toBeInstanceOf(Set)
    expect(result.current.sessionReadUrls.current.has("a")).toBe(true)
  })
})
