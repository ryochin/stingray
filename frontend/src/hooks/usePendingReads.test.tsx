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

describe("usePendingReads — flushReads failure recovery", (): void => {
  let warnSpy: ReturnType<typeof vi.spyOn>

  beforeEach((): void => {
    warnSpy = vi
      .spyOn(console, "warn")
      .mockImplementation((): void => undefined)
  })

  afterEach((): void => {
    warnSpy.mockRestore()
  })

  it("retries after a transient failure and succeeds on the next attempt", async (): Promise<void> => {
    markReadSpy
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValueOnce(undefined)

    const { result } = renderHook(
      (): ReturnType<typeof usePendingReads> => usePendingReads(),
      { wrapper },
    )

    act((): void => {
      result.current.scheduleRead("a")
    })

    // Initial flush at 500ms — first markRead rejects, retry timer arms.
    await act(async (): Promise<void> => {
      await vi.advanceTimersByTimeAsync(500)
    })
    expect(markReadSpy).toHaveBeenCalledTimes(1)
    expect(markReadSpy.mock.calls[0][0]).toEqual(["a"])

    // First retry slot is 2000ms; second attempt resolves.
    await act(async (): Promise<void> => {
      await vi.advanceTimersByTimeAsync(2000)
    })
    expect(markReadSpy).toHaveBeenCalledTimes(2)
    expect(markReadSpy.mock.calls[1][0]).toEqual(["a"])
  })

  it("exhausts retries, discards the queue, and logs a warning", async (): Promise<void> => {
    markReadSpy.mockRejectedValue(new Error("down"))

    const { result } = renderHook(
      (): ReturnType<typeof usePendingReads> => usePendingReads(),
      { wrapper },
    )

    act((): void => {
      result.current.scheduleRead("a")
    })

    // 1 initial flush (500ms debounce) + 5 retries at 2/4/8/16/30s.
    const delays: number[] = [500, 2000, 4000, 8000, 16000, 30000]
    for (const d of delays) {
      await act(async (): Promise<void> => {
        await vi.advanceTimersByTimeAsync(d)
      })
    }
    expect(markReadSpy).toHaveBeenCalledTimes(6)
    expect(warnSpy).toHaveBeenCalled()

    // After exhaustion, pendingReadUrls must be discarded — a fresh
    // scheduleRead("z") followed by a normal flush should send only "z".
    markReadSpy.mockResolvedValueOnce(undefined)
    act((): void => {
      result.current.scheduleRead("z")
    })
    await act(async (): Promise<void> => {
      await vi.advanceTimersByTimeAsync(500)
    })
    expect(markReadSpy).toHaveBeenCalledTimes(7)
    expect(markReadSpy.mock.calls[6][0]).toEqual(["z"])
  })

  it("merges URLs queued during retry back-off into the next batch", async (): Promise<void> => {
    markReadSpy
      .mockRejectedValueOnce(new Error("transient"))
      .mockResolvedValueOnce(undefined)

    const { result } = renderHook(
      (): ReturnType<typeof usePendingReads> => usePendingReads(),
      { wrapper },
    )

    act((): void => {
      result.current.scheduleRead("a")
    })
    await act(async (): Promise<void> => {
      await vi.advanceTimersByTimeAsync(500)
    })
    // First call sent ["a"], rejected. "a" is back in pendingReadUrls.
    expect(markReadSpy.mock.calls[0][0]).toEqual(["a"])

    // While in retry back-off, schedule a new URL — its 500ms debounce
    // fires first and the resulting flush includes the failed URL too.
    act((): void => {
      result.current.scheduleRead("b")
    })
    await act(async (): Promise<void> => {
      await vi.advanceTimersByTimeAsync(500)
    })
    expect(markReadSpy).toHaveBeenCalledTimes(2)
    expect(markReadSpy.mock.calls[1][0].sort()).toEqual(["a", "b"])
  })

  it("does not arm a retry timer when the failure happens after unmount", async (): Promise<void> => {
    markReadSpy.mockRejectedValueOnce(new Error("late"))

    const { result, unmount } = renderHook(
      (): ReturnType<typeof usePendingReads> => usePendingReads(),
      { wrapper },
    )

    act((): void => {
      result.current.scheduleRead("a")
    })
    // unmount synchronously calls flushReads; the .catch runs on a
    // microtask, after isUnmounted has been set.
    unmount()
    expect(markReadSpy).toHaveBeenCalledTimes(1)

    // Drain microtasks so the .catch path runs to completion, then
    // advance past every retry slot — no further calls should happen.
    await act(async (): Promise<void> => {
      await vi.advanceTimersByTimeAsync(60000)
    })
    expect(markReadSpy).toHaveBeenCalledTimes(1)
    expect(warnSpy).toHaveBeenCalled()
  })

  it("clears the retry timer once a subsequent flush succeeds", async (): Promise<void> => {
    markReadSpy
      .mockRejectedValueOnce(new Error("first"))
      .mockResolvedValueOnce(undefined)

    const { result } = renderHook(
      (): ReturnType<typeof usePendingReads> => usePendingReads(),
      { wrapper },
    )

    act((): void => {
      result.current.scheduleRead("a")
    })
    await act(async (): Promise<void> => {
      await vi.advanceTimersByTimeAsync(500)
    })
    expect(markReadSpy).toHaveBeenCalledTimes(1)

    // Retry timer is armed at 2000ms. Force a successful manual flush
    // before it fires — pendingReadUrls still contains "a".
    await act(async (): Promise<void> => {
      result.current.flushReads()
      // settle the .then so the retry counter resets cleanly.
      await Promise.resolve()
    })
    expect(markReadSpy).toHaveBeenCalledTimes(2)

    // Advance well past the original retry delay; no stale timer fires.
    await act(async (): Promise<void> => {
      await vi.advanceTimersByTimeAsync(5000)
    })
    expect(markReadSpy).toHaveBeenCalledTimes(2)
  })

  it("scheduleRead during retry back-off respects the full 500ms debounce", async (): Promise<void> => {
    markReadSpy
      .mockRejectedValueOnce(new Error("first"))
      .mockResolvedValueOnce(undefined)

    const { result } = renderHook(
      (): ReturnType<typeof usePendingReads> => usePendingReads(),
      { wrapper },
    )

    act((): void => {
      result.current.scheduleRead("a")
    })
    await act(async (): Promise<void> => {
      await vi.advanceTimersByTimeAsync(500)
    })
    expect(markReadSpy).toHaveBeenCalledTimes(1)
    // retryTimer is now armed at +2000ms from "now".

    // Advance 100ms, then schedule "z". flushTimer is now armed at
    // +500ms from this point (i.e. 100+500=600ms after the failure).
    act((): void => {
      vi.advanceTimersByTime(100)
    })
    act((): void => {
      result.current.scheduleRead("z")
    })
    // 499ms later: neither retry (1400ms remaining) nor flush has fired.
    act((): void => {
      vi.advanceTimersByTime(499)
    })
    expect(markReadSpy).toHaveBeenCalledTimes(1)

    // +1ms: flushTimer fires. The retry timer is still ~1399ms away and
    // must be cancelled by flushReads so it doesn't double-flush.
    await act(async (): Promise<void> => {
      await vi.advanceTimersByTimeAsync(1)
    })
    expect(markReadSpy).toHaveBeenCalledTimes(2)
    expect(markReadSpy.mock.calls[1][0].sort()).toEqual(["a", "z"])

    // Advance past the original retry slot — should stay at 2 calls.
    await act(async (): Promise<void> => {
      await vi.advanceTimersByTimeAsync(5000)
    })
    expect(markReadSpy).toHaveBeenCalledTimes(2)
  })
})
