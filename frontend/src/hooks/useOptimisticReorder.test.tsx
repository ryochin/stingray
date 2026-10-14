/**
 * The optimistic write itself. Rollback is covered through the screens
 * (FolderManager.test.tsx, useFeedMutations.test.tsx); this pins what they
 * cannot reach: no cached list yet, and a query key other than theirs.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, renderHook } from "@testing-library/react"
import type { JSX, ReactNode } from "react"
import { describe, expect, it, vi } from "vitest"
import { useOptimisticReorder } from "./useOptimisticReorder"

function makeClient(): {
  queryClient: QueryClient
  wrapper: ({ children }: { children: ReactNode }) => JSX.Element
} {
  const queryClient: QueryClient = new QueryClient({
    defaultOptions: { mutations: { retry: false } },
  })
  const wrapper = ({ children }: { children: ReactNode }): JSX.Element => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  return { queryClient, wrapper }
}

describe("useOptimisticReorder", (): void => {
  it("leaves an uncached list alone instead of inventing one", async (): Promise<void> => {
    const { queryClient, wrapper } = makeClient()
    const apply = vi.fn((prev: number[]): number[] => prev)
    const mutationFn = vi.fn((): Promise<void> => Promise.resolve())
    const { result } = renderHook(
      () =>
        useOptimisticReorder<number>({
          queryKey: ["items"],
          mutationFn,
          apply,
          onError: vi.fn(),
        }),
      { wrapper },
    )

    await act(async (): Promise<void> => {
      await result.current.mutateAsync([1, 2])
    })

    expect(mutationFn).toHaveBeenCalled()
    expect(apply).not.toHaveBeenCalled()
    expect(queryClient.getQueryData(["items"])).toBeUndefined()
  })

  it.each([
    ["succeeds", (): Promise<void> => Promise.resolve()],
    ["fails", (): Promise<void> => Promise.reject(new Error("nope"))],
  ])("refetches the given query once the reorder %s", async (_label, respond): Promise<void> => {
    const { queryClient, wrapper } = makeClient()
    queryClient.setQueryData<number[]>(["items"], [1, 2])
    const invalidate = vi.spyOn(queryClient, "invalidateQueries")
    const { result } = renderHook(
      () =>
        useOptimisticReorder<number>({
          queryKey: ["items"],
          mutationFn: respond,
          apply: (_prev: number[], ids: number[]): number[] => ids,
          onError: vi.fn(),
        }),
      { wrapper },
    )

    await act(async (): Promise<void> => {
      await result.current.mutateAsync([2, 1]).catch((): void => {})
    })

    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["items"] })
  })
})
