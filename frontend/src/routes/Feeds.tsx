import { useQuery, useQueryClient } from "@tanstack/react-query"
import type { JSX } from "react"
import { useCallback, useMemo, useState } from "react"
import type { Feed, FeedCreate, Folder } from "../api/client"
import { api } from "../api/client"
import AddFeedForm from "../components/AddFeedForm"
import type { FeedItemActions } from "../components/FeedItem"
import FeedList from "../components/FeedList"
import FolderManager from "../components/FolderManager"
import Header from "../components/Header"
import OpmlButtons from "../components/OpmlButtons"
import QueryErrorBanner from "../components/QueryErrorBanner"
import { useFeedMutations } from "../hooks/useFeedMutations"
import { failedLoads } from "../utils/queryErrors"

export default function Feeds(): JSX.Element {
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const [expandedFeeds, setExpandedFeeds] = useState<Set<number>>(new Set())
  const [filter, setFilter] = useState<string>("")
  const [errorFilter, setErrorFilter] = useState<"all" | "with" | "without">(
    "all",
  )

  // While a refresh is running, poll faster so per-feed stats (Unread,
  // Articles count, Latest published) reflect as each feed finishes — both
  // for OPML-triggered auto-refresh and manual refresh.
  const { data: status } = useQuery({
    queryKey: ["status"],
    queryFn: api.getStatus,
    refetchInterval: (query) => (query.state.data?.running ? 2_000 : 30_000),
  })
  const activeInterval: number = status?.running ? 3_000 : 15_000

  const {
    data: feeds,
    isLoading,
    isError,
  } = useQuery({
    queryKey: ["feeds"],
    queryFn: api.getFeeds,
    refetchInterval: activeInterval,
  })
  const { data: folders, isError: foldersError } = useQuery({
    queryKey: ["folders"],
    queryFn: api.getFolders,
    refetchInterval: activeInterval,
  })
  const { data: feedStats, isError: feedStatsError } = useQuery({
    queryKey: ["feed-stats"],
    queryFn: api.getFeedStats,
    refetchInterval: activeInterval,
  })

  const sortedFolders = useMemo(
    (): Folder[] =>
      (folders ?? [])
        .slice()
        .sort(
          (folderA: Folder, folderB: Folder): number =>
            folderA.position - folderB.position || folderA.id - folderB.id,
        ),
    [folders],
  )

  const reportError = useCallback(
    (err: Error): void => setError(err.message),
    [],
  )
  const mutations = useFeedMutations({ onError: setError })
  const {
    invalidate,
    fetchingFeeds,
    feedCandidates,
    candidatesFor,
    dismissCandidates,
    addFeed,
    toggleFeed,
    toggleSummarize,
    deleteFeed,
    fetchFeed,
    renameFeed,
    updateTranslate,
    updateSiteUrl,
    moveFeed,
    reorderFeeds,
  } = mutations

  // Filter by name/URL substring (case-insensitive) AND fetch-error status.
  // FeedList gets the unfiltered list too, so dragging preserves global
  // ordering even when the user has narrowed the view.
  // "with errors" matches Sidebar's !!! warning threshold (consecutive_failures >= 3).
  const visibleFeeds = useMemo((): Feed[] => {
    const query: string = filter.trim().toLowerCase()
    return (feeds ?? []).filter((feed: Feed): boolean => {
      if (
        query &&
        !feed.name.toLowerCase().includes(query) &&
        !(feed.url?.toLowerCase().includes(query) ?? false)
      ) {
        return false
      }
      if (errorFilter === "with" && feed.consecutive_failures < 3) return false
      if (errorFilter === "without" && feed.consecutive_failures >= 3)
        return false
      return true
    })
  }, [feeds, filter, errorFilter])

  const toggleExpand = useCallback((feedId: number): void => {
    setExpandedFeeds((prev: Set<number>): Set<number> => {
      const next: Set<number> = new Set(prev)
      if (next.has(feedId)) next.delete(feedId)
      else next.add(feedId)
      return next
    })
  }, [])

  // Add a feed and auto-expand its card so the user can edit/inspect it right
  // away. Shared by the main submit and the 422 "pick a candidate" path.
  const addFeedAndExpand = useCallback(
    (body: FeedCreate): Promise<Feed> =>
      addFeed.mutateAsync(body).then((feed: Feed): Feed => {
        setExpandedFeeds(
          (prev: Set<number>): Set<number> => new Set(prev).add(feed.id),
        )
        return feed
      }),
    [addFeed],
  )

  const handleRename = useCallback(
    (feedId: number, name: string): void => renameFeed.mutate({ feedId, name }),
    [renameFeed],
  )
  const handleDelete = useCallback(
    (feed: Feed): void => {
      if (confirm(`Delete "${feed.name}"?`)) deleteFeed.mutate(feed.id)
    },
    [deleteFeed],
  )
  const handleFetch = useCallback(
    (feedId: number): void => fetchFeed.mutate(feedId),
    [fetchFeed],
  )
  const handleToggleEnabled = useCallback(
    (feedId: number): void => toggleFeed.mutate(feedId),
    [toggleFeed],
  )
  const handleToggleSummarize = useCallback(
    (feedId: number): void => toggleSummarize.mutate(feedId),
    [toggleSummarize],
  )
  const handleMove = useCallback(
    (feedId: number, folderId: number | null): void =>
      moveFeed.mutate({ feedId, folderId }),
    [moveFeed],
  )
  const handleToggleTranslate = useCallback(
    (feedId: number, translate: boolean): void =>
      updateTranslate.mutate({ feedId, translate }),
    [updateTranslate],
  )
  const handleUpdateSiteUrl = useCallback(
    (feedId: number, siteUrl: string | null): void =>
      updateSiteUrl.mutate({ feedId, siteUrl }),
    [updateSiteUrl],
  )

  const feedActions: FeedItemActions = {
    onFetch: handleFetch,
    onDelete: handleDelete,
    onToggleEnabled: handleToggleEnabled,
    onToggleSummarize: handleToggleSummarize,
    onMoveToFolder: handleMove,
    onToggleTranslate: handleToggleTranslate,
    onUpdateSiteUrl: handleUpdateSiteUrl,
    onRulesUpdated: invalidate,
  }

  return (
    <div className="flex flex-col h-screen">
      <Header />
      <main className="flex-1 overflow-y-auto px-7 py-5 max-w-6xl">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-text-heading">Feeds</h2>
          <div className="flex items-center gap-2">
            <OpmlButtons
              onError={reportError}
              onImported={(): void => {
                invalidate()
                // OPML import auto-triggers a refresh on the backend. Nudge the
                // status query so the Header shows "Fetching..." immediately
                // instead of waiting up to 30s for its idle-mode poll.
                queryClient.invalidateQueries({ queryKey: ["status"] })
              }}
              feedCount={feeds?.length ?? 0}
            />
            <button
              type="button"
              onClick={(): void => {
                if (
                  confirm(
                    "Delete ALL feeds, folders, and articles? This cannot be undone.",
                  )
                ) {
                  api.deleteAllFeeds().then(invalidate).catch(reportError)
                }
              }}
              disabled={!feeds || feeds.length === 0}
              className="px-3 py-1.5 rounded text-sm bg-bg-card text-red-400 border border-border hover:bg-red-900 hover:text-red-200 transition-colors disabled:opacity-40 disabled:pointer-events-none"
            >
              Delete All
            </button>
          </div>
        </div>

        <FolderManager folders={sortedFolders} onError={reportError} />

        <AddFeedForm
          onAdd={addFeedAndExpand}
          isAdding={addFeed.isPending}
          folders={sortedFolders}
          candidates={feedCandidates}
          candidatesFor={candidatesFor}
          onDismissCandidates={dismissCandidates}
        />

        {(feeds?.length ?? 0) > 0 && (
          <div className="mb-4 flex items-center gap-2">
            <div className="relative flex-1">
              <svg
                className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <circle cx="11" cy="11" r="8" />
                <line x1="21" y1="21" x2="16.65" y2="16.65" />
              </svg>
              <input
                type="search"
                value={filter}
                onChange={(event: React.ChangeEvent<HTMLInputElement>): void =>
                  setFilter(event.target.value)
                }
                placeholder="Filter by name or URL..."
                className="w-full bg-bg-card text-text border border-border rounded pl-8 pr-3 py-1.5 text-sm focus:outline-none focus:border-accent"
              />
            </div>
            <select
              value={errorFilter}
              onChange={(event: React.ChangeEvent<HTMLSelectElement>): void =>
                setErrorFilter(event.target.value as "all" | "with" | "without")
              }
              className="bg-bg-card text-text border border-border rounded px-2 py-1.5 text-sm focus:outline-none focus:border-accent"
            >
              <option value="all">All feeds</option>
              <option value="with">With errors</option>
              <option value="without">No errors</option>
            </select>
            {(filter || errorFilter !== "all") && (
              <span className="text-xs text-text-muted">
                {visibleFeeds.length}/{feeds?.length ?? 0}
              </span>
            )}
          </div>
        )}

        {error && (
          <div role="alert" className="text-red-400 text-sm mb-4">
            {error}
          </div>
        )}

        <QueryErrorBanner
          failed={failedLoads({
            feeds: isError,
            folders: foldersError,
            "feed stats": feedStatsError,
          })}
        />
        {/* A failed background refetch keeps the cached list mounted, so an
            in-progress rename in FeedList survives until the next success. */}
        {!feeds ? (
          isLoading && <div className="text-text-muted">Loading...</div>
        ) : (
          <FeedList
            visibleFeeds={visibleFeeds}
            allFeeds={feeds}
            folders={sortedFolders}
            stats={feedStats}
            fetchingFeeds={fetchingFeeds}
            expandedFeeds={expandedFeeds}
            onToggleExpand={toggleExpand}
            onRename={handleRename}
            onReorder={(ids: number[]): void => reorderFeeds.mutate(ids)}
            actions={feedActions}
          />
        )}
      </main>
    </div>
  )
}
