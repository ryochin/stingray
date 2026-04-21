import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { api, ApiError } from "../api/client"
import type { Feed, FeedCreate, FeedCandidate } from "../api/client"

interface Options {
  /** Called with the user-facing error message whenever a mutation fails. */
  onError: (message: string) => void
}

/**
 * All feed/folder CRUD mutations plus the `invalidate` helper that refreshes
 * the three related queries (`feeds`, `folders`, `feed-stats`). Encapsulating
 * them here keeps the Feeds route focused on orchestration and rendering.
 *
 * `addFeed` additionally owns the "feed candidates" state: when the backend
 * responds 422 with candidate feeds (e.g. a site URL resolves to multiple
 * RSS endpoints), we surface them so the user can pick one.
 */
export function useFeedMutations({ onError }: Options) {
  const queryClient = useQueryClient()
  const [feedCandidates, setFeedCandidates] = useState<FeedCandidate[] | null>(null)
  const [candidatesFor, setCandidatesFor] = useState<string | null>(null)
  const [fetchingFeeds, setFetchingFeeds] = useState<Set<number>>(new Set())

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["feeds"] })
    queryClient.invalidateQueries({ queryKey: ["folders"] })
    queryClient.invalidateQueries({ queryKey: ["feed-stats"] })
  }
  const reportError = (e: Error) => onError(e.message)

  const addFeed = useMutation({
    mutationFn: api.createFeed,
    onMutate: (body: FeedCreate) => {
      setCandidatesFor(body.url)
    },
    onSuccess: () => {
      invalidate()
      setFeedCandidates(null)
      setCandidatesFor(null)
    },
    onError: (e: Error) => {
      if (e instanceof ApiError && e.status === 422) {
        const detail = (e.body as { detail?: { candidates?: FeedCandidate[] } } | null)?.detail
        if (detail?.candidates && detail.candidates.length > 0) {
          setFeedCandidates(detail.candidates)
          return
        }
      }
      setFeedCandidates(null)
      setCandidatesFor(null)
      reportError(e)
    },
  })

  const toggleFeed = useMutation({ mutationFn: api.toggleFeed, onSuccess: invalidate, onError: reportError })
  const toggleSummarize = useMutation({ mutationFn: api.toggleSummarize, onSuccess: invalidate, onError: reportError })
  const deleteFeed = useMutation({ mutationFn: api.deleteFeed, onSuccess: invalidate, onError: reportError })

  // fetchFeed shows a local "fetching" indicator per feed; the 5s delay before
  // clearing + invalidating gives the backend time to persist fetched articles.
  const fetchFeed = useMutation({
    mutationFn: api.fetchFeed,
    onMutate: (feedId: number) => {
      setFetchingFeeds((prev) => new Set(prev).add(feedId))
    },
    onSettled: (_data, _error, feedId) => {
      setTimeout(() => {
        setFetchingFeeds((prev) => {
          const next = new Set(prev)
          next.delete(feedId)
          return next
        })
        invalidate()
      }, 5000)
    },
    onError: reportError,
  })

  const renameFeed = useMutation({
    mutationFn: ({ feedId, name }: { feedId: number, name: string }) =>
      api.renameFeed(feedId, name),
    onSuccess: invalidate,
    onError: reportError,
  })
  const updateTranslate = useMutation({
    mutationFn: ({ feedId, translate }: { feedId: number, translate: boolean }) =>
      api.updateFeedTranslate(feedId, translate),
    onSuccess: invalidate,
    onError: reportError,
  })
  const moveFeed = useMutation({
    mutationFn: ({ feedId, folderId }: { feedId: number, folderId: number | null }) =>
      api.moveFeedToFolder(feedId, folderId),
    onSuccess: invalidate,
    onError: reportError,
  })

  // Optimistic reorder: splice the dragged feed into its new position inside
  // the cached list before the server responds. On error we roll back to the
  // pre-mutation snapshot captured in onMutate.
  const reorderFeeds = useMutation({
    mutationFn: (ids: number[]) => api.reorderFeeds(ids),
    onMutate: async (ids) => {
      await queryClient.cancelQueries({ queryKey: ["feeds"] })
      const prev = queryClient.getQueryData<Feed[]>(["feeds"])
      if (prev) {
        const idSet = new Set(ids)
        const byId = new Map(prev.map((f) => [f.id, f]))
        const reordered: Feed[] = []
        let cursor = 0
        for (const f of prev) {
          if (idSet.has(f.id)) {
            const next = byId.get(ids[cursor++])
            if (next) reordered.push(next)
          } else {
            reordered.push(f)
          }
        }
        queryClient.setQueryData<Feed[]>(["feeds"], reordered)
      }
      return { prev }
    },
    onError: (err, _ids, ctx) => {
      if (ctx?.prev) queryClient.setQueryData(["feeds"], ctx.prev)
      reportError(err as Error)
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["feeds"] })
    },
  })

  return {
    invalidate,
    feedCandidates,
    candidatesFor,
    dismissCandidates: () => {
      setFeedCandidates(null)
      setCandidatesFor(null)
    },
    fetchingFeeds,
    addFeed,
    toggleFeed,
    toggleSummarize,
    deleteFeed,
    fetchFeed,
    renameFeed,
    updateTranslate,
    moveFeed,
    reorderFeeds,
  }
}
