import { useQueryClient } from "@tanstack/react-query"
import type { MutableRefObject } from "react"
import { useCallback, useEffect, useRef, useState } from "react"
import type { Article } from "../api/client"
import { api } from "../api/client"

interface PendingReadsApi {
  /** Adds `url` to the pending batch (idempotent within a session). The
   *  batch is flushed to the server after a short debounce window. */
  scheduleRead: (url: string) => void
  /** Forces an immediate flush. Safe to call when the queue is empty. */
  flushReads: () => void
  /** Clears session-read state, e.g. when the active selection changes
   *  and previously-read articles should no longer be filtered out. The
   *  queued batch is sent first, so it never holds URLs outside the
   *  session set. */
  resetSessionReads: () => void
  /** Drops `urls` from the session-read set and the queued batch, e.g. when
   *  they are being marked unread — otherwise they would keep counting as
   *  reads the DB hasn't caught up with yet, and a later read of the same
   *  URL would be swallowed as a duplicate. */
  forgetSessionReads: (urls: Iterable<string>) => void
  /** URLs marked read during the current session — exposed so callers
   *  can pass the underlying Set to derivation helpers. */
  sessionReadUrls: MutableRefObject<Set<string>>
  /** Stable membership probe over `sessionReadUrls`. Prefer this over
   *  `sessionReadUrls.current.has(...)` at call sites — biome's hook-deps
   *  lint flags property-chain calls through destructured refs, while a
   *  stable callback satisfies the rule cleanly. */
  hasSessionRead: (url: string) => boolean
  /** Counter that ticks each time `scheduleRead` accepts a new URL or
   *  `forgetSessionReads` drops some, and resets with `resetSessionReads`.
   *  Mutations to `sessionReadUrls` are opaque to React, so consumers key
   *  memos off this counter to re-run when the set changes. */
  localReadCount: number
  /** Runs `run` (an unread write, optimistic update included) and registers
   *  it as in flight until it settles, so `waitForUnreads` callers hold off
   *  until the server has the article back as unread. */
  trackUnread: <T>(run: () => Promise<T>) => Promise<T>
  /** Resolves once no unread write is in flight, settled either way. Read
   *  writes wait here so they can't land before an earlier unread (the read
   *  update only touches `read_at IS NULL` rows, so the unread would win),
   *  and reads of read state wait so they can't clobber the optimistic
   *  update with the server's pre-unread rows. */
  waitForUnreads: () => Promise<void>
}

const FLUSH_DELAY_MS: number = 500
// Exponential backoff (capped at 30s) for transient markRead failures.
// After the final entry is consumed, the queued URLs are discarded.
const RETRY_DELAYS_MS: readonly number[] = [2000, 4000, 8000, 16000, 30000]

/** Batches `markRead` calls so rapid focus traversal doesn't fire one
 *  request per article. Tracks the session-read set locally so the UI can
 *  hide already-read items before the server round-trip resolves. */
export function usePendingReads(): PendingReadsApi {
  const queryClient = useQueryClient()
  const sessionReadUrls = useRef<Set<string>>(new Set())
  const [localReadCount, setLocalReadCount] = useState<number>(0)
  const pendingReadUrls = useRef<Set<string>>(new Set())
  const flushTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const retryAttempt = useRef<number>(0)
  const isUnmounted = useRef<boolean>(false)
  const inflightUnreads = useRef<Set<Promise<unknown>>>(new Set())
  const isWaitingForUnreads = useRef<boolean>(false)

  const waitForUnreads = useCallback(async (): Promise<void> => {
    // Loop: an unread started while waiting must be waited for too.
    while (inflightUnreads.current.size > 0) {
      await Promise.allSettled(inflightUnreads.current)
    }
  }, [])

  const trackUnread = useCallback(<T>(run: () => Promise<T>): Promise<T> => {
    const inflight: Promise<T> = run()
    inflightUnreads.current.add(inflight)
    // Registered before any caller's handlers, so the set is already
    // cleared by the time a waiter or the mutation's onSettled resumes.
    const untrack = (): void => {
      inflightUnreads.current.delete(inflight)
    }
    inflight.then(untrack, untrack)
    return inflight
  }, [])

  const flushReads = useCallback((): void => {
    if (pendingReadUrls.current.size === 0) return
    // Clear both timers up front so neither one re-enters flush while the
    // request is in flight — without this, a stale retryTimer could
    // short-circuit a later scheduleRead's 500ms debounce window.
    if (flushTimer.current) {
      clearTimeout(flushTimer.current)
      flushTimer.current = null
    }
    if (retryTimer.current) {
      clearTimeout(retryTimer.current)
      retryTimer.current = null
    }
    // Send after any in-flight unread instead. URLs queued meanwhile stay in
    // the queue and go out in the same batch; flushes triggered while
    // waiting are absorbed by the one already scheduled.
    if (inflightUnreads.current.size > 0) {
      if (isWaitingForUnreads.current) return
      isWaitingForUnreads.current = true
      void waitForUnreads().then((): void => {
        isWaitingForUnreads.current = false
        flushReads()
      })
      return
    }
    const inflight: string[] = Array.from(pendingReadUrls.current)
    pendingReadUrls.current.clear()
    api
      .markRead(inflight)
      .then(async (): Promise<void> => {
        retryAttempt.current = 0
        // Unread mode is small, so refetch it. Time-range modes can return
        // tens of MB ("All" caps at 10,000 articles), so stamp `read_at` in
        // the cache instead. Stamp only after feed-stats has refetched: the
        // sidebar subtracts session reads whose cached `read_at` is still
        // null, so stamping earlier would bump the badges back up until
        // the new counts arrive.
        queryClient.invalidateQueries({
          queryKey: ["articles", { unread: true }],
        })
        await queryClient.invalidateQueries({ queryKey: ["feed-stats"] })
        const targets: Set<string> = new Set(inflight)
        const readAt: string = new Date().toISOString()
        queryClient.setQueriesData<Article[]>(
          { queryKey: ["articles", { unread: false }] },
          (prev: Article[] | undefined): Article[] | undefined =>
            prev?.map(
              (a: Article): Article =>
                targets.has(a.url) && a.read_at == null
                  ? { ...a, read_at: readAt }
                  : a,
            ),
        )
      })
      .catch((err: unknown): void => {
        // Merge back so the next flush retries the batch. sessionReadUrls
        // is intentionally untouched to avoid UI flicker on the article
        // list — failures are recovered server-side via retry, not by
        // un-hiding the optimistically-read card.
        for (const url of inflight) pendingReadUrls.current.add(url)
        if (isUnmounted.current) {
          console.warn("markRead failed after unmount; reads dropped", err)
          return
        }
        const attempt: number = retryAttempt.current
        if (attempt >= RETRY_DELAYS_MS.length) {
          // Discard the queue too — otherwise the next scheduleRead /
          // manual flush / unmount flush would silently restart the
          // backoff sequence with stale URLs.
          console.warn("markRead retry exhausted; dropping reads", err)
          pendingReadUrls.current.clear()
          retryAttempt.current = 0
          return
        }
        retryAttempt.current = attempt + 1
        retryTimer.current = setTimeout(flushReads, RETRY_DELAYS_MS[attempt])
      })
  }, [queryClient, waitForUnreads])

  const scheduleRead = useCallback(
    (url: string): void => {
      if (!sessionReadUrls.current.has(url)) {
        pendingReadUrls.current.add(url)
        sessionReadUrls.current.add(url)
        setLocalReadCount((count: number): number => count + 1)
      }
      if (flushTimer.current) clearTimeout(flushTimer.current)
      flushTimer.current = setTimeout(flushReads, FLUSH_DELAY_MS)
    },
    [flushReads],
  )

  // Clearing the ref alone is opaque to React; resetting `localReadCount`
  // is what actually invalidates downstream memos that key off the count.
  // Flushing first keeps the queue a subset of the session set, which
  // `forgetSessionReads` callers rely on to reach every queued read in the
  // current selection.
  const resetSessionReads = useCallback((): void => {
    flushReads()
    sessionReadUrls.current.clear()
    setLocalReadCount(0)
  }, [flushReads])

  const forgetSessionReads = useCallback((urls: Iterable<string>): void => {
    let dropped: boolean = false
    for (const url of urls) {
      pendingReadUrls.current.delete(url)
      if (sessionReadUrls.current.delete(url)) dropped = true
    }
    if (dropped) setLocalReadCount((count: number): number => count + 1)
  }, [])

  const hasSessionRead = useCallback(
    (url: string): boolean => sessionReadUrls.current.has(url),
    [],
  )

  // Flush on unmount so a tab close mid-debounce doesn't drop reads.
  // isUnmounted is set so a post-unmount markRead failure is logged
  // without arming a retry timer that can't fire.
  useEffect(
    (): (() => void) => (): void => {
      isUnmounted.current = true
      if (flushTimer.current) clearTimeout(flushTimer.current)
      if (retryTimer.current) clearTimeout(retryTimer.current)
      flushReads()
    },
    [flushReads],
  )

  return {
    scheduleRead,
    flushReads,
    resetSessionReads,
    forgetSessionReads,
    sessionReadUrls,
    hasSessionRead,
    localReadCount,
    trackUnread,
    waitForUnreads,
  }
}
