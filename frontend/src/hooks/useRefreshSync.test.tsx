import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, renderHook } from "@testing-library/react"
import type { JSX, ReactNode } from "react"
import type { MockInstance } from "vitest"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { useRefreshSync } from "./useRefreshSync"

const SYNCED_KEYS: string[][] = [
  ["articles"],
  ["feeds"],
  ["folders"],
  ["feed-stats"],
]

let queryClient: QueryClient
let invalidate: MockInstance

function wrapper({ children }: { children: ReactNode }): JSX.Element {
  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

function renderSync(initial: boolean | undefined) {
  return renderHook(
    ({ running }: { running: boolean | undefined }): void =>
      useRefreshSync(running),
    { wrapper, initialProps: { running: initial } },
  )
}

function expectSyncedOnce(): void {
  expect(invalidate).toHaveBeenCalledTimes(SYNCED_KEYS.length)
  for (const queryKey of SYNCED_KEYS) {
    expect(invalidate).toHaveBeenCalledWith({ queryKey })
  }
}

beforeEach((): void => {
  queryClient = new QueryClient()
  invalidate = vi.spyOn(queryClient, "invalidateQueries")
})

afterEach((): void => {
  cleanup()
  queryClient.clear()
})

describe("useRefreshSync", (): void => {
  it.each([
    ["undefined", undefined],
    ["false", false],
  ])("does not invalidate on an initial %s", (_: string, running:
    | boolean
    | undefined): void => {
    renderSync(running)
    expect(invalidate).not.toHaveBeenCalled()
  })

  it("invalidates on an initial true (treated as an idle→running edge)", (): void => {
    renderSync(true)
    expectSyncedOnce()
  })

  it("invalidates on each running↔idle transition", (): void => {
    const { rerender } = renderSync(false)

    rerender({ running: true })
    expectSyncedOnce()

    invalidate.mockClear()
    rerender({ running: false })
    expectSyncedOnce()
  })

  it("does not invalidate when the flag is unchanged", (): void => {
    const { rerender } = renderSync(true)
    invalidate.mockClear()

    rerender({ running: true })
    expect(invalidate).not.toHaveBeenCalled()
  })

  it("treats undefined as idle", (): void => {
    const { rerender } = renderSync(false)
    rerender({ running: undefined })
    expect(invalidate).not.toHaveBeenCalled()

    rerender({ running: true })
    invalidate.mockClear()
    rerender({ running: undefined })
    expectSyncedOnce()
  })
})
