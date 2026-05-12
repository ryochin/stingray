import { useQueryClient } from "@tanstack/react-query"
import type { MutableRefObject } from "react"
import { useCallback, useEffect, useRef, useState } from "react"
import { api } from "../api/client"

interface PendingReadsApi {
  /** Adds `url` to the pending batch (idempotent within a session). The
   *  batch is flushed to the server after a short debounce window. */
  scheduleRead: (url: string) => void
  /** Forces an immediate flush. Safe to call when the queue is empty. */
  flushReads: () => void
  /** Clears session-read state, e.g. when the active selection changes
   *  and previously-read articles should no longer be filtered out. */
  resetSessionReads: () => void
  /** URLs marked read during the current session — exposed so callers
   *  can pass the underlying Set to derivation helpers. */
  sessionReadUrls: MutableRefObject<Set<string>>
  /** Stable membership probe over `sessionReadUrls`. Prefer this over
   *  `sessionReadUrls.current.has(...)` at call sites — biome's hook-deps
   *  lint flags property-chain calls through destructured refs, while a
   *  stable callback satisfies the rule cleanly. */
  hasSessionRead: (url: string) => boolean
  /** Monotonic counter that ticks each time `scheduleRead` accepts a new
   *  URL. Mutations to `sessionReadUrls` are opaque to React, so consumers
   *  key memos off this counter to re-run when the set changes. */
  localReadCount: number
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
    const inflight: string[] = Array.from(pendingReadUrls.current)
    pendingReadUrls.current.clear()
    api
      .markRead(inflight)
      .then((): void => {
        retryAttempt.current = 0
        queryClient.invalidateQueries({ queryKey: ["articles"] })
        queryClient.invalidateQueries({ queryKey: ["feed-stats"] })
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
  }, [queryClient])

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
  const resetSessionReads = useCallback((): void => {
    sessionReadUrls.current.clear()
    setLocalReadCount(0)
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
    sessionReadUrls,
    hasSessionRead,
    localReadCount,
  }
}
