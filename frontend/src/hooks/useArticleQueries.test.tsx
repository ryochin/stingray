/**
 * Guards the `sinceDays` derivation in <useArticleQueries> — the single point
 * where the reader's unread toggle and time-range selection turn into a
 * backend query parameter. The range options and the default id are covered
 * as pure functions in `articleView.test.ts`; this file covers the wiring
 * that decides whether the window is applied at all, and that switching it
 * refetches instead of serving a differently-scoped cache entry.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, renderHook, waitFor } from "@testing-library/react"
import type { JSX, ReactNode } from "react"
import type { MockInstance } from "vitest"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { Article } from "../api/client"
import { api } from "../api/client"
import {
  DEFAULT_TIME_RANGE_ID,
  TIME_RANGE_OPTIONS,
  type TimeRangeId,
  type TimeRangeOption,
} from "../utils/articleView"
import { useArticleQueries } from "./useArticleQueries"

type GetArticlesOpts = NonNullable<Parameters<typeof api.getArticles>[0]>

/** Every offered range paired with the day count it must send. */
const RANGE_CASES: ReadonlyArray<[TimeRangeId, number | null]> =
  TIME_RANGE_OPTIONS.map(
    (opt: TimeRangeOption): [TimeRangeId, number | null] => [opt.id, opt.days],
  )

let getArticlesSpy: MockInstance<typeof api.getArticles>

function makeWrapper(): ({ children }: { children: ReactNode }) => JSX.Element {
  // One client per render tree so cache state never leaks between cases.
  const queryClient: QueryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return function Wrapper({ children }: { children: ReactNode }): JSX.Element {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
  }
}

/** Render the hook once and report the `sinceDays` the articles fetch used. */
async function fetchedSinceDays(
  showUnreadOnly: boolean,
  timeRangeId: TimeRangeId,
): Promise<number | null | undefined> {
  renderHook(
    (): ReturnType<typeof useArticleQueries> =>
      useArticleQueries({ showUnreadOnly, timeRangeId }),
    { wrapper: makeWrapper() },
  )
  await waitFor((): void => {
    expect(getArticlesSpy).toHaveBeenCalled()
  })
  const opts: GetArticlesOpts | undefined = getArticlesSpy.mock.calls[0]?.[0]
  return opts?.sinceDays
}

beforeEach((): void => {
  getArticlesSpy = vi
    .spyOn(api, "getArticles")
    .mockResolvedValue([] as Article[])
  // The hook fires four other queries in parallel; stub them so nothing
  // reaches the real fetch and the polling intervals stay inert.
  vi.spyOn(api, "getStatus").mockResolvedValue({
    running: false,
    last_started_at: null,
    last_finished_at: null,
    last_status: null,
    last_new_count: null,
    last_error: null,
    llm_enabled: false,
    llm_available: false,
    llm_error: null,
  })
  vi.spyOn(api, "getFeeds").mockResolvedValue([])
  vi.spyOn(api, "getFolders").mockResolvedValue([])
  vi.spyOn(api, "getFeedStats").mockResolvedValue({})
})

afterEach((): void => {
  cleanup()
  vi.restoreAllMocks()
})

describe("useArticleQueries — sinceDays derivation", (): void => {
  it("omits the time filter in unread mode so old unread items stay reachable", async (): Promise<void> => {
    // Even though a bounded range is selected, unread mode must ignore it.
    expect(await fetchedSinceDays(true, DEFAULT_TIME_RANGE_ID)).toBeNull()
  })

  it("applies the default window once all articles are shown", async (): Promise<void> => {
    expect(await fetchedSinceDays(false, DEFAULT_TIME_RANGE_ID)).toBe(3)
  })

  it("omits the time filter when the reader picks the All range", async (): Promise<void> => {
    expect(await fetchedSinceDays(false, "all")).toBeNull()
  })

  for (const [id, days] of RANGE_CASES) {
    it(`requests ${id} as sinceDays=${days} when all articles are shown`, async (): Promise<void> => {
      expect(await fetchedSinceDays(false, id)).toBe(days)
    })
  }
})

describe("useArticleQueries — refetching on range change", (): void => {
  it("refetches with the new window instead of reusing the cached response", async (): Promise<void> => {
    const { rerender } = renderHook(
      ({
        showUnreadOnly,
        timeRangeId,
      }: {
        showUnreadOnly: boolean
        timeRangeId: TimeRangeId
      }): ReturnType<typeof useArticleQueries> =>
        useArticleQueries({ showUnreadOnly, timeRangeId }),
      {
        wrapper: makeWrapper(),
        initialProps: {
          showUnreadOnly: true,
          timeRangeId: DEFAULT_TIME_RANGE_ID,
        },
      },
    )

    await waitFor((): void => {
      expect(getArticlesSpy).toHaveBeenCalledWith({ sinceDays: null })
    })

    // Leaving unread mode activates the selected window: a distinct query key,
    // so the unbounded response must not be reused for the bounded view.
    rerender({ showUnreadOnly: false, timeRangeId: DEFAULT_TIME_RANGE_ID })
    await waitFor((): void => {
      expect(getArticlesSpy).toHaveBeenCalledWith({ sinceDays: 3 })
    })

    rerender({ showUnreadOnly: false, timeRangeId: "30d" })
    await waitFor((): void => {
      expect(getArticlesSpy).toHaveBeenCalledWith({ sinceDays: 30 })
    })
  })
})
