import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import type { VirtualItem } from "@tanstack/react-virtual"
import { useVirtualizer } from "@tanstack/react-virtual"
import type { JSX } from "react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { Feed, Selection } from "../api/client"
import { api, faviconUrl } from "../api/client"
import ArticleCard from "../components/ArticleCard"
import CaughtUpIndicator from "../components/CaughtUpIndicator"
import Header from "../components/Header"
import MarkAllReadMenu from "../components/MarkAllReadMenu"
import ShortcutsHelp from "../components/ShortcutsHelp"
import Sidebar from "../components/Sidebar"
import { useArticleKeyboard } from "../hooks/useArticleKeyboard"
import { useFocusStabilizer } from "../hooks/useFocusStabilizer"
import {
  applyUnreadFilter,
  computeFolderFeedOrder,
  deriveUnreadCounts,
  nextUnreadFeedId,
  parseTimeRangeId,
  selectArticles,
  TIME_RANGE_OPTIONS,
  type TimeRangeId,
  tallySessionReadByFeed,
  timeRangeDays,
} from "../utils/articleView"
import { smoothScrollTo } from "../utils/smoothScroll"

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
    setSelection(sel)
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
  const sessionReadUrls = useRef<Set<string>>(new Set())
  const [localReadCount, setLocalReadCount] = useState<number>(0)
  const articleRefs = useRef<Map<number, HTMLDivElement>>(new Map())
  const mainRef = useRef<HTMLElement>(null)
  const stickyHeaderRef = useRef<HTMLDivElement>(null)
  const stickySentinelRef = useRef<HTMLDivElement>(null)
  const [isHeaderStuck, setIsHeaderStuck] = useState<boolean>(false)
  const [caughtUpPulseKey, setCaughtUpPulseKey] = useState<number>(0)
  // Tracks the rAF driving the focus-scroll animation so competing scroll
  // sources (e.g. onJAtEnd's scrollTo bottom) can cancel it before issuing
  // their own scroll — otherwise the rAF keeps writing main.scrollTop each
  // frame and overrides the new scroll intent.
  const focusScrollRafRef = useRef<number | null>(null)

  // Debounced batch read marking
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
      flushTimer.current = setTimeout(flushReads, 500)
    },
    [flushReads],
  )

  // Flush on unmount
  useEffect(
    (): (() => void) => (): void => {
      if (flushTimer.current) clearTimeout(flushTimer.current)
      flushReads()
    },
    [flushReads],
  )

  // Subscribe to status so refetchInterval reacts immediately when a refresh
  // starts/ends, instead of waiting for the next scheduled tick. Header also
  // owns a status observer — both share the same cache key.
  const { data: status } = useQuery({
    queryKey: ["status"],
    queryFn: api.getStatus,
    refetchInterval: (query) => (query.state.data?.running ? 2_000 : 30_000),
  })
  const running: boolean = status?.running ?? false

  // While a refresh is running, poll faster so per-feed unread counts reflect
  // as each feed finishes (OPML import, manual refresh).
  const activeInterval: number = running ? 3_000 : 15_000

  // Unread mode bypasses the time filter: the user wants every still-unread
  // item to be reachable regardless of age. Otherwise the backend trims to
  // the selected window so the response naturally matches the visible list.
  const sinceDays: number | null = showUnreadOnly
    ? null
    : timeRangeDays(timeRangeId)

  const {
    data: allArticles,
    isLoading,
    isError,
  } = useQuery({
    queryKey: ["articles", { sinceDays }],
    queryFn: (): ReturnType<typeof api.getArticles> =>
      api.getArticles({ sinceDays }),
    refetchInterval: activeInterval,
  })

  const { data: feeds } = useQuery({
    queryKey: ["feeds"],
    queryFn: api.getFeeds,
    refetchInterval: activeInterval,
  })

  // Both transitions (idle→running, running→idle) are owned by `useRefreshSync`
  // mounted from `Header`, which the route renders. The shared QueryClient
  // cache means we don't need our own copy here.

  const { data: folders } = useQuery({
    queryKey: ["folders"],
    queryFn: api.getFolders,
  })

  // Per-feed unread totals from the DB (not from the time-bounded /articles
  // response). This is the source of truth for sidebar badges so they stay
  // consistent regardless of the active time range.
  const { data: feedStats } = useQuery({
    queryKey: ["feed-stats"],
    queryFn: api.getFeedStats,
    refetchInterval: activeInterval,
  })

  const feedMap = useMemo((): Map<number, Feed> => {
    const map: Map<number, Feed> = new Map<number, Feed>()
    for (const feed of feeds ?? []) map.set(feed.id, feed)
    return map
  }, [feeds])

  // Ordered feed list matching sidebar display order
  const orderedFeedIds = useMemo((): number[] => {
    if (!feeds || !folders) return []
    const enabled: Feed[] = feeds.filter((feed: Feed): boolean => feed.enabled)
    const sortedFolders = folders
      .slice()
      .sort(
        (folderA, folderB): number =>
          folderA.position - folderB.position || folderA.id - folderB.id,
      )
    const ids: number[] = []
    for (const folder of sortedFolders) {
      for (const feed of enabled) {
        if (feed.folder_id === folder.id) ids.push(feed.id)
      }
    }
    for (const feed of enabled) {
      if (feed.folder_id == null) ids.push(feed.id)
    }
    return ids
  }, [feeds, folders])

  const enabledFeedIds = useMemo((): Set<number> | null => {
    if (!feeds) return null
    return new Set(
      feeds
        .filter((feed: Feed): boolean => feed.enabled)
        .map((feed: Feed): number => feed.id),
    )
  }, [feeds])

  const summarizeFeedIds = useMemo((): Set<number> => {
    if (!feeds) return new Set<number>()
    return new Set(
      feeds
        .filter((feed: Feed): boolean => feed.summarize)
        .map((feed: Feed): number => feed.id),
    )
  }, [feeds])

  const enabledArticles = useMemo(() => {
    if (!enabledFeedIds) return allArticles ?? []
    return (allArticles ?? []).filter(
      (article): boolean =>
        article.feed_id != null && enabledFeedIds.has(article.feed_id),
    )
  }, [allArticles, enabledFeedIds])

  // Decrement stats-derived unread counts by URLs the user just marked read
  // locally, so the sidebar reflects the change before the next stats refetch.
  const sessionReadByFeed = useMemo((): Map<number, number> => {
    void localReadCount
    return tallySessionReadByFeed(enabledArticles, sessionReadUrls.current)
  }, [enabledArticles, localReadCount])

  const unreadCounts = useMemo(
    () => deriveUnreadCounts(feedStats, enabledFeedIds, sessionReadByFeed),
    [feedStats, enabledFeedIds, sessionReadByFeed],
  )

  const folderFeedOrder = useMemo(
    () => computeFolderFeedOrder(selection, feeds),
    [selection, feeds],
  )

  const filtered = useMemo(() => {
    void localReadCount // sessionReadUrls mutations are opaque to React; re-run when they tick.
    const selected = selectArticles(enabledArticles, selection, folderFeedOrder)
    // Time-range filtering happens at the API layer (sinceDays). The list we
    // got is already bounded; here we only layer the unread toggle on top.
    return applyUnreadFilter(selected, showUnreadOnly, sessionReadUrls.current)
  }, [
    enabledArticles,
    selection,
    folderFeedOrder,
    showUnreadOnly,
    localReadCount,
  ])

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

  // Clear session reads and reset focus when selection changes. Resetting
  // `localReadCount` is what actually invalidates the `sessionReadByFeed`
  // memo — clearing the ref alone is opaque to React, so the cached tally
  // would otherwise leak the previous selection's reads into the new view.
  useEffect((): void => {
    sessionReadUrls.current.clear()
    setLocalReadCount(0)
  }, [])

  // Clear focus when feed/filter changes and scroll back to the top.
  // A separate effect below picks up from -1 and focuses the first article
  // once data is available (covers both mount and selection change, since
  // articles are often still loading at the moment selection changes).
  useEffect((): void => {
    setFocusIndex(-1)
    mainRef.current?.scrollTo({ top: 0 })
  }, [])

  // Auto-focus the first article whenever focus is cleared and there is
  // something to focus. This makes opening the articles view land on the
  // top unread item without requiring an initial j press.
  useEffect((): void => {
    if (focusIndex === -1 && filtered.length > 0) {
      setFocusIndex(0)
    }
  }, [focusIndex, filtered.length])

  // Detect when the header block becomes "stuck" to the top so we can shrink
  // the title. A zero-height sentinel placed just above the sticky wrapper
  // stops intersecting the scroll root the moment the header sticks.
  useEffect((): (() => void) | undefined => {
    const sentinel: HTMLDivElement | null = stickySentinelRef.current
    const main: HTMLElement | null = mainRef.current
    if (!sentinel || !main) return
    const observer: IntersectionObserver = new IntersectionObserver(
      ([entry]: IntersectionObserverEntry[]): void =>
        setIsHeaderStuck(!entry.isIntersecting),
      { root: main, threshold: 0 },
    )
    observer.observe(sentinel)
    return (): void => observer.disconnect()
  }, [])

  // Track sticky header height so the virtualizer can offset its scroll
  // origin by exactly that amount (scrollMargin). Re-measured via
  // ResizeObserver to cover stuck transitions, wrap changes, font load, etc.
  const [headerHeight, setHeaderHeight] = useState<number>(0)
  useEffect((): (() => void) | undefined => {
    const el: HTMLDivElement | null = stickyHeaderRef.current
    if (!el) return
    const update = (): void =>
      setHeaderHeight(el.getBoundingClientRect().height)
    update()
    const observer: ResizeObserver = new ResizeObserver(update)
    observer.observe(el)
    return (): void => observer.disconnect()
  }, [])

  // Virtualize the article card list. All rendering modes (all / folder /
  // feed) share the same list so virtualization is unconditional.
  // Gap between cards is realised as padding-bottom on the virtualizer
  // wrapper div so measureElement's bounding rect includes it naturally
  // without overriding measurement.
  const CARD_GAP = 16
  // Include the "All caught up" sentinel as the last virtual item so its
  // position is coordinated with the virtualizer's scrollAdjustments /
  // smooth-scroll state. Otherwise it sits in normal flow after the
  // container and jitters as items below the viewport get measured for
  // the first time (the classic dynamic-size virtualizer tail wobble).
  const ALL_CAUGHT_UP_INDEX = filtered.length
  const virtualizer = useVirtualizer({
    count: filtered.length + 1,
    getScrollElement: () => mainRef.current,
    // Most cards land around 180-220px tall; a closer estimate reduces
    // the delta applied when an overscan item is first measured, which
    // in turn dampens the jitter of totalSize-driven layout shifts.
    // The sentinel row is shorter (~100px icon+text+padding).
    estimateSize: (index: number): number =>
      index === ALL_CAUGHT_UP_INDEX ? 100 : 200 + CARD_GAP,
    // Wider overscan keeps more items measured before they reach the
    // viewport edge, further reducing first-measure churn.
    overscan: 12,
    scrollMargin: headerHeight,
    // Batch ResizeObserver callbacks into RAF to coalesce bursts of
    // measurements (e.g. images loading across several visible cards).
    useAnimationFrameWithResizeObserver: true,
    getItemKey: (index: number): string | number =>
      index === ALL_CAUGHT_UP_INDEX
        ? "__all_caught_up__"
        : (filtered[index]?.url ?? index),
  })

  const skipFocusScroll = useFocusStabilizer({
    filtered,
    focusIndex,
    setFocusIndex,
    virtualizer,
    mainRef,
    selection,
    showUnreadOnly,
    timeRangeId,
  })

  // Scroll focused article into view (custom rAF smooth scroll for tunable duration)
  useEffect((): (() => void) | undefined => {
    if (focusIndex < 0) return
    if (skipFocusScroll.current) {
      skipFocusScroll.current = false
      return
    }
    const main: HTMLElement | null = mainRef.current
    if (!main) return
    const el: HTMLDivElement | undefined = articleRefs.current.get(focusIndex)
    // Out-of-range (virtualized away): jump with virtualizer and let the
    // next render place the card; the rAF smooth path below handles the
    // in-range case. For the first article, bypass the virtualizer —
    // its scrollMargin would land us at headerHeight instead of 0,
    // leaving the sticky header in its stuck (small) state.
    if (!el) {
      if (focusIndex === 0) {
        main.scrollTop = 0
      } else {
        virtualizer.scrollToIndex(focusIndex, { align: "start" })
      }
      return
    }
    // For the first article, scroll all the way to the top so the sticky
    // header releases and returns to its initial (full-size) state.
    // Otherwise, align the article's top with the sticky header's bottom
    // edge so the article is never occluded by the header.
    let target: number
    if (focusIndex === 0) {
      target = 0
    } else {
      const headerBottom: number = stickyHeaderRef.current
        ? stickyHeaderRef.current.getBoundingClientRect().bottom
        : main.getBoundingClientRect().top
      target = main.scrollTop + el.getBoundingClientRect().top - headerBottom
    }
    smoothScrollTo(main, target, { rafRef: focusScrollRafRef })
    return (): void => {
      if (focusScrollRafRef.current != null) {
        cancelAnimationFrame(focusScrollRafRef.current)
        focusScrollRafRef.current = null
      }
    }
  }, [focusIndex, virtualizer, skipFocusScroll.current, skipFocusScroll])

  // Mark article as read when focus leaves it
  const markFocusedAsRead = useCallback(
    (index: number): void => {
      if (index < 0 || index >= filtered.length) return
      const article = filtered[index]
      if (article && article.read_at == null) {
        scheduleRead(article.url)
      }
    },
    [filtered, scheduleRead],
  )

  // Find the next still-unread article after `after` in the current view.
  // Session-read items count as read so the user is not redirected to an
  // article they just dismissed via j/m.
  const nextUnreadInView = useCallback(
    (after: number): number => {
      for (let index = after + 1; index < filtered.length; index++) {
        const article = filtered[index]
        if (
          article.read_at == null &&
          !sessionReadUrls.current.has(article.url)
        )
          return index
      }
      return -1
    },
    [filtered],
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

  const goToNextFeed = useCallback((): boolean => {
    // Reference feed: in feed selection it is obviously the selected feed;
    // in all/folder selection we use the feed_id of the currently focused
    // article so "jump to next unread feed" remains meaningful.
    let refFeedId: number | null = null
    if (selection.type === "feed") {
      refFeedId = selection.id
    } else if (focusIndex >= 0 && focusIndex < filtered.length) {
      refFeedId = filtered[focusIndex].feed_id
    }
    if (refFeedId == null) return false
    const next: number | null = nextUnreadFeedId(
      orderedFeedIds,
      refFeedId,
      unreadCounts,
    )
    if (next == null) return false
    updateSelection({ type: "feed", id: next })
    return true
  }, [
    selection,
    orderedFeedIds,
    unreadCounts,
    updateSelection,
    focusIndex,
    filtered,
  ])

  const onJAtEnd = useCallback((): void => {
    // Cancel any in-flight focus-scroll rAF. Without this, the still-running
    // rAF keeps writing main.scrollTop each frame and silently overrides the
    // smooth scroll-to-bottom we issue below.
    if (focusScrollRafRef.current != null) {
      cancelAnimationFrame(focusScrollRafRef.current)
      focusScrollRafRef.current = null
    }
    setCaughtUpPulseKey((key: number): number => key + 1)
    const main: HTMLElement | null = mainRef.current
    if (!main) return
    // Go through virtualizer.scrollToOffset (not main.scrollTo) so that
    // `scrollState.behavior === "smooth"` is set on the virtualizer.
    // While smooth scrolling, the virtualizer suppresses its scrollAdjust-
    // ment writes on item-size changes; bypassing the virtualizer causes
    // those writes to jump main.scrollTop mid-animation, producing a
    // visible up/down jitter instead of a clean scroll to the bottom.
    virtualizer.scrollToOffset(main.scrollHeight, { behavior: "smooth" })
  }, [virtualizer])

  // When k is pressed while the focused card's top has scrolled above the
  // sticky header (e.g. after j-at-end scrolled to the bottom), re-align the
  // current card instead of moving focus to the previous one.
  const onKBeforeMove = useCallback((): boolean => {
    const main: HTMLElement | null = mainRef.current
    if (!main || focusIndex < 0) return false
    const el: HTMLDivElement | undefined = articleRefs.current.get(focusIndex)
    // Virtualized away: the card isn't in the DOM, so it's definitely not
    // aligned. Jump to it with the virtualizer and stay on this index.
    if (!el) {
      virtualizer.scrollToIndex(focusIndex, { align: "start" })
      return true
    }
    const headerBottom: number = stickyHeaderRef.current
      ? stickyHeaderRef.current.getBoundingClientRect().bottom
      : main.getBoundingClientRect().top
    const cardTop: number = el.getBoundingClientRect().top
    if (cardTop >= headerBottom - 4) return false
    const target: number =
      focusIndex === 0 ? 0 : main.scrollTop + cardTop - headerBottom
    // Shared ref means the focus-scroll effect and this realign can preempt
    // each other safely instead of leaking concurrent rAF loops.
    smoothScrollTo(main, target, { rafRef: focusScrollRafRef })
    return true
  }, [focusIndex, virtualizer])

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

  const setRef = useCallback(
    (index: number, el: HTMLDivElement | null): void => {
      if (el) {
        articleRefs.current.set(index, el)
      } else {
        articleRefs.current.delete(index)
      }
    },
    [],
  )

  const handleCardClick = useCallback(
    (index: number): void => {
      setFocusIndex((prev: number): number => {
        if (prev !== index) markFocusedAsRead(prev)
        return index
      })
    },
    [markFocusedAsRead],
  )

  const selectionHeader = useMemo((): {
    icon: string | null
    label: string
    feedUrl: string | null
  } | null => {
    if (selection.type === "feed") {
      const feed: Feed | undefined = feedMap.get(selection.id)
      if (!feed) return null
      return { icon: faviconUrl(feed), label: feed.name, feedUrl: feed.url }
    }
    if (selection.type === "folder") {
      const label: string | null =
        folders?.find((folder): boolean => folder.id === selection.id)?.name ??
        null
      return label ? { icon: null, label, feedUrl: null } : null
    }
    return null
  }, [selection, feedMap, folders])

  // Unread count for the header `Unread (N)` button. `filtered` already
  // applied selection + (in unread mode) the unread filter, so iterate that
  // directly. Session-read items are kept in `filtered` (intentional, so the
  // user can still see what they just dismissed) — exclude them here so the
  // count reflects only true unreads.
  const selectedUnreadInView = useMemo((): number => {
    void localReadCount
    let count: number = 0
    for (const article of filtered) {
      if (article.read_at == null && !sessionReadUrls.current.has(article.url))
        count++
    }
    return count
  }, [filtered, localReadCount])

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
              <div
                style={{
                  // `getTotalSize()` already nets out `scrollMargin`
                  // (see virtual-core: `end - scrollMargin + paddingEnd`)
                  // so this is exactly `sum(measured)` — the right value
                  // for a container whose first card sits at y=0.
                  height: virtualizer.getTotalSize(),
                  position: "relative",
                  width: "100%",
                }}
              >
                {virtualizer.getVirtualItems().map((vi: VirtualItem) => {
                  if (vi.index === ALL_CAUGHT_UP_INDEX) {
                    return (
                      <div
                        key={vi.key}
                        ref={virtualizer.measureElement}
                        data-index={vi.index}
                        style={{
                          position: "absolute",
                          top: 0,
                          left: 0,
                          width: "100%",
                          transform: `translateY(${vi.start - virtualizer.options.scrollMargin}px)`,
                        }}
                      >
                        <CaughtUpIndicator
                          key={caughtUpPulseKey}
                          label="All caught up"
                          className={`origin-center ${
                            caughtUpPulseKey > 0
                              ? "text-text animate-caught-up-pulse"
                              : "text-text-dim/60"
                          }`}
                        />
                      </div>
                    )
                  }
                  const article = filtered[vi.index]
                  if (!article) return null
                  const feed: Feed | undefined =
                    article.feed_id != null
                      ? feedMap.get(article.feed_id)
                      : undefined
                  return (
                    <div
                      key={vi.key}
                      ref={virtualizer.measureElement}
                      data-index={vi.index}
                      style={{
                        position: "absolute",
                        top: 0,
                        left: 0,
                        width: "100%",
                        paddingBottom: CARD_GAP,
                        transform: `translateY(${vi.start - virtualizer.options.scrollMargin}px)`,
                      }}
                    >
                      <ArticleCard
                        article={article}
                        focused={vi.index === focusIndex}
                        pendingSummary={
                          !article.summary &&
                          !article.content_translated &&
                          article.feed_id != null &&
                          summarizeFeedIds.has(article.feed_id)
                        }
                        feedName={feed?.name}
                        feedFaviconUrl={feed ? faviconUrl(feed) : null}
                        ref={(el: HTMLDivElement | null): void =>
                          setRef(vi.index, el)
                        }
                        onClick={(): void => handleCardClick(vi.index)}
                      />
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        </main>
      </div>

      {showHelp && <ShortcutsHelp onClose={() => setShowHelp(false)} />}
    </div>
  )
}
