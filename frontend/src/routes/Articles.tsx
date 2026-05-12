import { useMutation, useQueryClient } from "@tanstack/react-query"
import type { JSX } from "react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { Feed, Selection } from "../api/client"
import { api } from "../api/client"
import ArticleList from "../components/ArticleList"
import CaughtUpIndicator from "../components/CaughtUpIndicator"
import Header from "../components/Header"
import MarkAllReadMenu from "../components/MarkAllReadMenu"
import ShortcutsHelp from "../components/ShortcutsHelp"
import Sidebar from "../components/Sidebar"
import { useArticleDerivedState } from "../hooks/useArticleDerivedState"
import { useArticleKeyboard } from "../hooks/useArticleKeyboard"
import { useArticleListController } from "../hooks/useArticleListController"
import { useArticleQueries } from "../hooks/useArticleQueries"
import { useElementHeight } from "../hooks/useElementHeight"
import { usePendingReads } from "../hooks/usePendingReads"
import { useSelectionHeader } from "../hooks/useSelectionHeader"
import { useStickyHeader } from "../hooks/useStickyHeader"
import {
  nextUnreadFeedId,
  parseTimeRangeId,
  TIME_RANGE_OPTIONS,
  type TimeRangeId,
} from "../utils/articleView"

export default function Articles(): JSX.Element {
  const queryClient = useQueryClient()
  const [selection, setSelection] = useState<Selection>((): Selection => {
    try {
      const saved: string | null = sessionStorage.getItem("feed-selection")
      if (saved) return JSON.parse(saved) as Selection
    } catch {}
    return { type: "all" }
  })
  const updateSelection = useCallback((sel: Selection): void => {
    // Same-value guard: callers (e.g. Sidebar) construct a fresh object on
    // every click, so reference equality fails for re-clicks of the active
    // item. Without this guard, `selection`-keyed effects (session reads
    // clear, scroll-to-top, focus reset) would re-fire on every re-click.
    setSelection((prev: Selection): Selection => {
      if (prev.type === sel.type) {
        if (sel.type === "all") return prev
        if (
          (sel.type === "feed" || sel.type === "folder") &&
          prev.type === sel.type &&
          (prev as { id: number }).id === sel.id
        ) {
          return prev
        }
      }
      return sel
    })
    sessionStorage.setItem("feed-selection", JSON.stringify(sel))
  }, [])
  const [showUnreadOnly, setShowUnreadOnly] = useState<boolean>(true)
  const [timeRangeId, setTimeRangeId] = useState<TimeRangeId>(
    (): TimeRangeId => {
      try {
        return parseTimeRangeId(sessionStorage.getItem("time-range"))
      } catch {
        return "all"
      }
    },
  )
  const updateTimeRangeId = useCallback((id: TimeRangeId): void => {
    setTimeRangeId(id)
    sessionStorage.setItem("time-range", id)
  }, [])
  const [focusIndex, setFocusIndex] = useState<number>(-1)
  const [showHelp, setShowHelp] = useState<boolean>(false)
  const {
    scheduleRead,
    resetSessionReads,
    sessionReadUrls,
    hasSessionRead,
    localReadCount,
  } = usePendingReads()
  const mainRef = useRef<HTMLElement>(null)
  const stickyHeaderRef = useRef<HTMLDivElement>(null)
  const stickySentinelRef = useRef<HTMLDivElement>(null)

  const {
    allArticles,
    articlesLoading: isLoading,
    articlesError: isError,
    feeds,
    folders,
    feedStats,
  } = useArticleQueries({ showUnreadOnly, timeRangeId })

  // Both transitions (idle→running, running→idle) are owned by `useRefreshSync`
  // mounted from `Header`, which the route renders. The shared QueryClient
  // cache means we don't need our own copy here.

  const { feedMap, orderedFeedIds, summarizeFeedIds, unreadCounts, filtered } =
    useArticleDerivedState({
      feeds,
      folders,
      feedStats,
      allArticles,
      selection,
      showUnreadOnly,
      sessionReadUrls,
      localReadCount,
    })

  // Validate restored selection against current data
  useEffect((): void => {
    if (!feeds || !folders) return
    if (
      selection.type === "feed" &&
      !feeds.some((feed: Feed): boolean => feed.id === selection.id)
    ) {
      updateSelection({ type: "all" })
    } else if (
      selection.type === "folder" &&
      !folders.some((folder): boolean => folder.id === selection.id)
    ) {
      updateSelection({ type: "all" })
    }
  }, [feeds, folders, selection, updateSelection])

  // Clear session reads when selection changes so previously-read articles
  // from another view don't leak into the new selection's filtered list.
  // biome-ignore lint/correctness/useExhaustiveDependencies: `selection` is a trigger; the body only invokes a stable reset and doesn't read it.
  useEffect((): void => {
    resetSessionReads()
  }, [selection])

  // Clear focus when feed/filter changes and scroll back to the top.
  // A separate effect below picks up from -1 and focuses the first article
  // once data is available (covers both mount and selection change, since
  // articles are often still loading at the moment selection changes).
  // biome-ignore lint/correctness/useExhaustiveDependencies: deps are change triggers; the body resets focus/scroll without reading them.
  useEffect((): void => {
    setFocusIndex(-1)
    mainRef.current?.scrollTo({ top: 0 })
  }, [selection, showUnreadOnly, timeRangeId])

  // Auto-focus the first article whenever focus is cleared and there is
  // something to focus. This makes opening the articles view land on the
  // top unread item without requiring an initial j press.
  useEffect((): void => {
    if (focusIndex === -1 && filtered.length > 0) {
      setFocusIndex(0)
    }
  }, [focusIndex, filtered.length])

  // Detect when the header block becomes "stuck" to the top so we can shrink
  // the title. The sentinel above the sticky wrapper drives the flag.
  const isHeaderStuck: boolean = useStickyHeader(stickySentinelRef, mainRef)

  // Track sticky header height so the virtualizer can offset its scroll
  // origin by exactly that amount (scrollMargin). Re-measured on layout
  // shifts (stuck transitions, wrap changes, font load, etc.).
  const headerHeight: number = useElementHeight(stickyHeaderRef)

  // Resolve the "reference feed" used by both `goToNextFeed` and the j-at-end
  // hint. In feed selection it is the selected feed itself; otherwise the
  // feed of the currently focused article so "next unread feed" stays
  // anchored to where the user is reading.
  const referenceFeedId: number | null = useMemo((): number | null => {
    if (selection.type === "feed") return selection.id
    if (focusIndex >= 0 && focusIndex < filtered.length) {
      return filtered[focusIndex].feed_id
    }
    return null
  }, [selection, focusIndex, filtered])

  const nextUnreadFeed: number | null = useMemo((): number | null => {
    if (referenceFeedId == null) return null
    return nextUnreadFeedId(orderedFeedIds, referenceFeedId, unreadCounts)
  }, [referenceFeedId, orderedFeedIds, unreadCounts])

  const goToNextFeed = useCallback((): boolean => {
    if (nextUnreadFeed == null) return false
    updateSelection({ type: "feed", id: nextUnreadFeed })
    return true
  }, [nextUnreadFeed, updateSelection])

  const {
    virtualizer,
    allCaughtUpIndex,
    setRef,
    handleCardClick,
    markFocusedAsRead,
    onJAtEnd,
    onKBeforeMove,
    caughtUpPulseKey,
    caughtUpHint,
  } = useArticleListController({
    filtered,
    focusIndex,
    setFocusIndex,
    mainRef,
    stickyHeaderRef,
    headerHeight,
    selection,
    showUnreadOnly,
    timeRangeId,
    scheduleRead,
    hasSessionRead,
    nextUnreadFeed,
  })

  // Find the next still-unread article after `after` in the current view.
  // Session-read items count as read so the user is not redirected to an
  // article they just dismissed via j/m. Kept inline here because it's
  // only consumed by the keyboard hook below.
  const nextUnreadInView = useCallback(
    (after: number): number => {
      for (let index = after + 1; index < filtered.length; index++) {
        const article = filtered[index]
        if (article.read_at == null && !hasSessionRead(article.url))
          return index
      }
      return -1
    },
    [filtered, hasSessionRead],
  )

  // Mark all as read mutation
  const markAllReadFeedId: number | undefined =
    selection.type === "feed" ? selection.id : undefined
  const markAllRead = useMutation({
    mutationFn: (olderThanHours: number | null) =>
      api.markAllRead(markAllReadFeedId, olderThanHours ?? undefined),
    onSuccess: (): void => {
      queryClient.invalidateQueries({ queryKey: ["articles"] })
      queryClient.invalidateQueries({ queryKey: ["feed-stats"] })
    },
  })
  const markAllUnread = useMutation({
    mutationFn: () => api.markAllUnread(markAllReadFeedId),
    onSuccess: (): void => {
      queryClient.invalidateQueries({ queryKey: ["articles"] })
      queryClient.invalidateQueries({ queryKey: ["feed-stats"] })
    },
  })

  // Toggle read/unread for a single article
  const toggleReadMutation = useMutation({
    mutationFn: ({ url, isRead }: { url: string; isRead: boolean }) =>
      isRead ? api.markUnread([url]) : api.markRead([url]),
    onSuccess: (): void => {
      queryClient.invalidateQueries({ queryKey: ["articles"] })
      queryClient.invalidateQueries({ queryKey: ["feed-stats"] })
    },
    onError: (err: Error): void => {
      console.error("Failed to toggle read status:", err)
    },
  })
  const toggleRead = useCallback(
    (url: string, isRead: boolean): void => {
      toggleReadMutation.mutate({ url, isRead })
    },
    [toggleReadMutation],
  )

  useArticleKeyboard({
    filtered,
    focusIndex,
    setFocusIndex,
    markFocusedAsRead,
    nextUnreadInView,
    scheduleRead,
    toggleRead,
    markAllRead: () => markAllRead.mutate(null),
    goToNextFeed,
    onJAtEnd,
    onKBeforeMove,
    toggleUnreadFilter: (): void =>
      setShowUnreadOnly((prev: boolean): boolean => !prev),
    setShowHelp,
  })

  // Map the controller's hint state into a presentation string here so
  // `<ArticleList>` doesn't need to know about the "jump"/"end" vocabulary.
  const caughtUpSubLabel: string | undefined =
    caughtUpHint === "jump"
      ? "Press <Space> key to jump to next unread feed"
      : caughtUpHint === "end"
        ? "No more unread feeds"
        : undefined

  const { selectionHeader, selectedUnreadInView } = useSelectionHeader({
    selection,
    feedMap,
    folders,
    filtered,
    localReadCount,
    hasSessionRead,
  })

  return (
    <div className="flex flex-col h-screen">
      <Header />
      <div className="flex flex-1 min-h-0">
        <Sidebar
          selection={selection}
          onSelect={updateSelection}
          unreadCounts={unreadCounts}
        />
        <main
          ref={mainRef}
          className="flex-1 overflow-y-auto px-4 pb-2 flex flex-col items-center"
        >
          <div className="w-[95%] max-w-4xl pl-1">
            <div ref={stickySentinelRef} aria-hidden className="h-0" />
            <div
              ref={stickyHeaderRef}
              className="sticky top-0 z-10 bg-bg pt-4 mb-4"
            >
              {selectionHeader && (
                <h2
                  className={`group font-medium text-text-heading flex items-center gap-2 transition-[font-size,padding] duration-200 ease-out ${
                    isHeaderStuck ? "text-lg py-1" : "text-2xl py-2"
                  }`}
                >
                  {selectionHeader.icon && (
                    <img
                      src={selectionHeader.icon}
                      alt=""
                      className={`transition-[width,height] duration-200 ease-out ${
                        isHeaderStuck ? "w-4 h-4" : "w-5 h-5"
                      }`}
                      loading="lazy"
                    />
                  )}
                  {selectionHeader.label}
                  {selectionHeader.feedUrl && (
                    <a
                      href={selectionHeader.feedUrl}
                      target="_blank"
                      rel="noopener"
                      aria-label="Open feed source"
                      title="Open feed source"
                      className="text-text-muted hover:text-text opacity-0 group-hover:opacity-100 focus-visible:opacity-100 transition-[opacity,color]"
                    >
                      <svg
                        className={`transition-[width,height] duration-200 ease-out ${
                          isHeaderStuck ? "w-3.5 h-3.5" : "w-4 h-4"
                        }`}
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        role="img"
                      >
                        <title>Open feed source</title>
                        <path d="M4 11a9 9 0 0 1 9 9" />
                        <path d="M4 4a16 16 0 0 1 16 16" />
                        <circle cx="5" cy="19" r="1" />
                      </svg>
                    </a>
                  )}
                </h2>
              )}
              <div className="flex items-center justify-between pb-2">
                <div className="flex items-center gap-2 text-sm">
                  <div className="flex gap-1">
                    <button
                      type="button"
                      onClick={() => setShowUnreadOnly(true)}
                      className={`px-3 py-1 rounded transition-colors ${
                        showUnreadOnly
                          ? "bg-accent-bg text-accent-text"
                          : "bg-bg-card text-text-muted hover:text-text"
                      }`}
                    >
                      Unread
                      {selectedUnreadInView > 0
                        ? ` (${selectedUnreadInView})`
                        : ""}
                    </button>
                    <button
                      type="button"
                      onClick={() => setShowUnreadOnly(false)}
                      className={`px-3 py-1 rounded transition-colors ${
                        !showUnreadOnly
                          ? "bg-warn-bg text-warn-text"
                          : "bg-bg-card text-text-muted hover:text-text"
                      }`}
                    >
                      All
                    </button>
                  </div>
                  <select
                    value={timeRangeId}
                    onChange={(
                      event: React.ChangeEvent<HTMLSelectElement>,
                    ): void =>
                      updateTimeRangeId(parseTimeRangeId(event.target.value))
                    }
                    disabled={showUnreadOnly}
                    className="px-2 py-1 rounded transition-colors outline-none border border-transparent focus:border-border bg-bg-card text-text-muted disabled:opacity-40 disabled:cursor-not-allowed"
                    aria-label="Time range filter"
                  >
                    {TIME_RANGE_OPTIONS.map(
                      (opt: (typeof TIME_RANGE_OPTIONS)[number]) => (
                        <option key={opt.id} value={opt.id}>
                          {opt.label}
                        </option>
                      ),
                    )}
                  </select>
                </div>
                {filtered.length > 0 && (
                  <MarkAllReadMenu
                    disabled={markAllRead.isPending || markAllUnread.isPending}
                    onChoose={(hours: number | null): void =>
                      markAllRead.mutate(hours)
                    }
                    onChooseUnread={() => markAllUnread.mutate()}
                  />
                )}
              </div>
            </div>

            {isError ? (
              <div className="text-red-400">Failed to load articles.</div>
            ) : isLoading ? (
              <div className="text-text-muted">Loading...</div>
            ) : filtered.length === 0 ? (
              showUnreadOnly ? (
                <CaughtUpIndicator label="No unread articles" />
              ) : (
                <div className="text-text-muted">No articles</div>
              )
            ) : (
              <ArticleList
                articles={filtered}
                focusIndex={focusIndex}
                feedMap={feedMap}
                summarizeFeedIds={summarizeFeedIds}
                virtualizer={virtualizer}
                allCaughtUpIndex={allCaughtUpIndex}
                setRef={setRef}
                onCardClick={handleCardClick}
                onTitleClick={scheduleRead}
                caughtUpPulseKey={caughtUpPulseKey}
                caughtUpSubLabel={caughtUpSubLabel}
              />
            )}
          </div>
        </main>
      </div>

      {showHelp && <ShortcutsHelp onClose={() => setShowHelp(false)} />}
    </div>
  )
}
