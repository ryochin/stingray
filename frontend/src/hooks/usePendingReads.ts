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

/** Batches `markRead` calls so rapid focus traversal doesn't fire one
 *  request per article. Tracks the session-read set locally so the UI can
 *  hide already-read items before the server round-trip resolves. */
export function usePendingReads(): PendingReadsApi {
  const queryClient = useQueryClient()
  const sessionReadUrls = useRef<Set<string>>(new Set())
  const [localReadCount, setLocalReadCount] = useState<number>(0)
  const pendingReadUrls = useRef<Set<string>>(new Set())
  const flushTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const flushReads = useCallback((): void => {
    if (pendingReadUrls.current.size === 0) return
    const urls: string[] = Array.from(pendingReadUrls.current)
    pendingReadUrls.current.clear()
    api.markRead(urls).then((): void => {
      queryClient.invalidateQueries({ queryKey: ["articles"] })
      queryClient.invalidateQueries({ queryKey: ["feed-stats"] })
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
  useEffect(
    (): (() => void) => (): void => {
      if (flushTimer.current) clearTimeout(flushTimer.current)
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
