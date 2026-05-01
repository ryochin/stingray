import { useState, useMemo, useEffect, useLayoutEffect, useRef, useCallback } from "react"
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query"
import { useVirtualizer } from "@tanstack/react-virtual"
import { api, faviconUrl } from "../api/client"
import type { Feed, Selection } from "../api/client"
import Header from "../components/Header"
import Sidebar from "../components/Sidebar"
import ArticleCard from "../components/ArticleCard"
import CaughtUpIndicator from "../components/CaughtUpIndicator"
import MarkAllReadMenu from "../components/MarkAllReadMenu"
import ShortcutsHelp from "../components/ShortcutsHelp"
import { useArticleKeyboard } from "../hooks/useArticleKeyboard"
import {
  applyUnreadFilter,
  computeFolderFeedOrder,
  deriveUnreadCounts,
  nextUnreadFeedId,
  parseTimeRangeId,
  selectArticles,
  tallySessionReadByFeed,
  timeRangeDays,
  TIME_RANGE_OPTIONS,
  type TimeRangeId,
} from "../utils/articleView"

export default function Articles() {
  const queryClient = useQueryClient()
  const [selection, setSelection] = useState<Selection>(() => {
    try {
      const saved = sessionStorage.getItem("feed-selection")
      if (saved) return JSON.parse(saved) as Selection
    } catch {}
    return { type: "all" }
  })
  const updateSelection = useCallback((sel: Selection) => {
    setSelection(sel)
    sessionStorage.setItem("feed-selection", JSON.stringify(sel))
  }, [])
  const [showUnreadOnly, setShowUnreadOnly] = useState(true)
  const [timeRangeId, setTimeRangeId] = useState<TimeRangeId>(() => {
    try {
      return parseTimeRangeId(sessionStorage.getItem("time-range"))
    } catch {
      return "all"
    }
  })
  const updateTimeRangeId = useCallback((id: TimeRangeId) => {
    setTimeRangeId(id)
    sessionStorage.setItem("time-range", id)
  }, [])
  const [focusIndex, setFocusIndex] = useState(-1)
  const [showHelp, setShowHelp] = useState(false)
  const sessionReadUrls = useRef<Set<string>>(new Set())
  const [localReadCount, setLocalReadCount] = useState(0)
  const articleRefs = useRef<Map<number, HTMLDivElement>>(new Map())
  const mainRef = useRef<HTMLElement>(null)
  const stickyHeaderRef = useRef<HTMLDivElement>(null)
  const stickySentinelRef = useRef<HTMLDivElement>(null)
  const [isHeaderStuck, setIsHeaderStuck] = useState(false)
  const [caughtUpPulseKey, setCaughtUpPulseKey] = useState(0)
  // Tracks the rAF driving the focus-scroll animation so competing scroll
  // sources (e.g. onJAtEnd's scrollTo bottom) can cancel it before issuing
  // their own scroll — otherwise the rAF keeps writing main.scrollTop each
  // frame and overrides the new scroll intent.
  const focusScrollRafRef = useRef<number | null>(null)

  // Debounced batch read marking
  const pendingReadUrls = useRef<Set<string>>(new Set())
  const flushTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const flushReads = useCallback(() => {
    if (pendingReadUrls.current.size === 0) return
    const urls = Array.from(pendingReadUrls.current)
    pendingReadUrls.current.clear()
    api.markRead(urls).then(() => {
      queryClient.invalidateQueries({ queryKey: ["articles"] })
      queryClient.invalidateQueries({ queryKey: ["feed-stats"] })
    })
  }, [queryClient])

  const scheduleRead = useCallback((url: string) => {
    if (!sessionReadUrls.current.has(url)) {
      pendingReadUrls.current.add(url)
      sessionReadUrls.current.add(url)
      setLocalReadCount((c) => c + 1)
    }
    if (flushTimer.current) clearTimeout(flushTimer.current)
    flushTimer.current = setTimeout(flushReads, 500)
  }, [flushReads])

  // Flush on unmount
  useEffect(() => () => {
    if (flushTimer.current) clearTimeout(flushTimer.current)
    flushReads()
  }, [flushReads])

  // Subscribe to status so refetchInterval reacts immediately when a refresh
  // starts/ends, instead of waiting for the next scheduled tick. Header also
  // owns a status observer — both share the same cache key.
  const { data: status } = useQuery({
    queryKey: ["status"],
    queryFn: api.getStatus,
    refetchInterval: (query) => (query.state.data?.running ? 2_000 : 30_000),
  })
  const running = status?.running ?? false

  // While a refresh is running, poll faster so per-feed unread counts reflect
  // as each feed finishes (OPML import, manual refresh).
  const activeInterval = running ? 3_000 : 15_000

  // Unread mode bypasses the time filter: the user wants every still-unread
  // item to be reachable regardless of age. Otherwise the backend trims to
  // the selected window so the response naturally matches the visible list.
  const sinceDays = showUnreadOnly ? null : timeRangeDays(timeRangeId)

  const { data: allArticles, isLoading, isError } = useQuery({
    queryKey: ["articles", { sinceDays }],
    queryFn: () => api.getArticles({ sinceDays }),
    refetchInterval: activeInterval,
  })

  const { data: feeds } = useQuery({
    queryKey: ["feeds"],
    queryFn: api.getFeeds,
    refetchInterval: activeInterval,
  })

  // When a refresh begins, force an immediate refetch so the sidebar reflects
  // the first finished feed without waiting for the next poll tick.
  const prevRunning = useRef(false)
  useEffect(() => {
    if (running && !prevRunning.current) {
      queryClient.invalidateQueries({ queryKey: ["articles"] })
      queryClient.invalidateQueries({ queryKey: ["feeds"] })
      queryClient.invalidateQueries({ queryKey: ["feed-stats"] })
    }
    prevRunning.current = running
  }, [running, queryClient])

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

  const feedMap = useMemo(() => {
    const map = new Map<number, Feed>()
    for (const feed of feeds ?? []) map.set(feed.id, feed)
    return map
  }, [feeds])

  // Ordered feed list matching sidebar display order
  const orderedFeedIds = useMemo(() => {
    if (!feeds || !folders) return []
    const enabled = feeds.filter((f) => f.enabled)
    const sortedFolders = folders.slice().sort((a, b) => a.position - b.position || a.id - b.id)
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

  const enabledFeedIds = useMemo(() => {
    if (!feeds) return null
    return new Set(feeds.filter((f) => f.enabled).map((f) => f.id))
  }, [feeds])

  const summarizeFeedIds = useMemo(() => {
    if (!feeds) return new Set<number>()
    return new Set(feeds.filter((f) => f.summarize).map((f) => f.id))
  }, [feeds])

  const enabledArticles = useMemo(() => {
    if (!enabledFeedIds) return allArticles ?? []
    return (allArticles ?? []).filter(
      (a) => a.feed_id != null && enabledFeedIds.has(a.feed_id)
    )
  }, [allArticles, enabledFeedIds])

  // Decrement stats-derived unread counts by URLs the user just marked read
  // locally, so the sidebar reflects the change before the next stats refetch.
  const sessionReadByFeed = useMemo(() => {
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
  }, [enabledArticles, selection, folderFeedOrder, showUnreadOnly, localReadCount])

  // Validate restored selection against current data
  useEffect(() => {
    if (!feeds || !folders) return
    if (selection.type === "feed" && !feeds.some((f) => f.id === selection.id)) {
      updateSelection({ type: "all" })
    } else if (selection.type === "folder" && !folders.some((f) => f.id === selection.id)) {
      updateSelection({ type: "all" })
    }
  }, [feeds, folders])

  // Clear session reads and reset focus when selection changes. Resetting
  // `localReadCount` is what actually invalidates the `sessionReadByFeed`
  // memo — clearing the ref alone is opaque to React, so the cached tally
  // would otherwise leak the previous selection's reads into the new view.
  useEffect(() => {
    sessionReadUrls.current.clear()
    setLocalReadCount(0)
  }, [selection])

  // Clear focus when feed/filter changes and scroll back to the top.
  // A separate effect below picks up from -1 and focuses the first article
  // once data is available (covers both mount and selection change, since
  // articles are often still loading at the moment selection changes).
  useEffect(() => {
    setFocusIndex(-1)
    mainRef.current?.scrollTo({ top: 0 })
  }, [selection, showUnreadOnly, timeRangeId])

  // Auto-focus the first article whenever focus is cleared and there is
  // something to focus. This makes opening the articles view land on the
  // top unread item without requiring an initial j press.
  useEffect(() => {
    if (focusIndex === -1 && filtered.length > 0) {
      setFocusIndex(0)
    }
  }, [focusIndex, filtered.length])

  // Detect when the header block becomes "stuck" to the top so we can shrink
  // the title. A zero-height sentinel placed just above the sticky wrapper
  // stops intersecting the scroll root the moment the header sticks.
  useEffect(() => {
    const sentinel = stickySentinelRef.current
    const main = mainRef.current
    if (!sentinel || !main) return
    const observer = new IntersectionObserver(
      ([entry]) => setIsHeaderStuck(!entry.isIntersecting),
      { root: main, threshold: 0 },
    )
    observer.observe(sentinel)
    return () => observer.disconnect()
  }, [])

  // Track sticky header height so the virtualizer can offset its scroll
  // origin by exactly that amount (scrollMargin). Re-measured via
  // ResizeObserver to cover stuck transitions, wrap changes, font load, etc.
  const [headerHeight, setHeaderHeight] = useState(0)
  useEffect(() => {
    const el = stickyHeaderRef.current
    if (!el) return
    const update = () => setHeaderHeight(el.getBoundingClientRect().height)
    update()
    const observer = new ResizeObserver(update)
    observer.observe(el)
    return () => observer.disconnect()
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
    estimateSize: (i) => i === ALL_CAUGHT_UP_INDEX ? 100 : 200 + CARD_GAP,
    // Wider overscan keeps more items measured before they reach the
    // viewport edge, further reducing first-measure churn.
    overscan: 12,
    scrollMargin: headerHeight,
    // Batch ResizeObserver callbacks into RAF to coalesce bursts of
    // measurements (e.g. images loading across several visible cards).
    useAnimationFrameWithResizeObserver: true,
    getItemKey: (index) =>
      index === ALL_CAUGHT_UP_INDEX ? "__all_caught_up__" : (filtered[index]?.url ?? index),
  })

  // Preserve focus identity and visual position when `filtered` shifts
  // (e.g. background refetch prepends new articles). Without this, the
  // focused card slides out from under the user and the viewport appears
  // to jump since scrollTop is preserved but cards are pushed down.
  //
  // Scope: only `filtered` changes caused by background data updates.
  // `selection` / `showUnreadOnly` toggles trigger their own reset effect
  // (focus → -1, scrollTo top); running compensation there would leak
  // `skipFocusScroll` into an unrelated scroll pass.
  const prevFocusSnapshot = useRef<{
    filtered: typeof filtered
    index: number
    url: string
    offset: number
  } | null>(null)
  const prevSelectionRef = useRef(selection)
  const prevShowUnreadOnlyRef = useRef(showUnreadOnly)
  const prevTimeRangeIdRef = useRef(timeRangeId)
  // Set right before a programmatic focusIndex change whose scroll the
  // subsequent focus-scroll effect must NOT override (the compensation
  // below already places the card visually).
  const skipFocusScroll = useRef(false)
  useLayoutEffect(() => {
    const main = mainRef.current
    const prev = prevFocusSnapshot.current
    const selectionChanged = prevSelectionRef.current !== selection
    const filterToggled = prevShowUnreadOnlyRef.current !== showUnreadOnly
    const timeRangeChanged = prevTimeRangeIdRef.current !== timeRangeId
    prevSelectionRef.current = selection
    prevShowUnreadOnlyRef.current = showUnreadOnly
    prevTimeRangeIdRef.current = timeRangeId

    // User-initiated list reset is handled elsewhere; drop the snapshot so
    // the next refetch-driven change re-captures from the post-reset state.
    if (selectionChanged || filterToggled || timeRangeChanged) {
      prevFocusSnapshot.current = null
      return
    }

    // Compensation only applies when the user's focus stayed put but the
    // list shifted beneath it. If focusIndex changed from the snapshot,
    // the focus move itself was user- or program-initiated (j/k, click,
    // auto-focus) and no rebinding is needed — reverting to prev.url
    // would cancel that move (e.g. j-advance whose scheduleRead forces
    // `filtered` to re-memo into a new reference).
    if (
      main
      && prev
      && prev.filtered !== filtered
      && focusIndex === prev.index
      && focusIndex >= 0
      && filtered[focusIndex]?.url !== prev.url
    ) {
      const newIndex = filtered.findIndex((a) => a.url === prev.url)
      if (newIndex < 0) {
        // Focused article vanished (server-side delete, read state changed
        // outside this session, etc). Avoid silently inheriting whichever
        // article slid into the old index slot; clear the snapshot and let
        // the normal render continue with focusIndex at its numeric slot.
        prevFocusSnapshot.current = null
        return
      }
      const newOffset = virtualizer.getOffsetForIndex(newIndex, "start")?.[0]
      if (newOffset != null) {
        main.scrollTop += newOffset - prev.offset
      }
      skipFocusScroll.current = true
      setFocusIndex(newIndex)
      prevFocusSnapshot.current = {
        filtered,
        index: newIndex,
        url: prev.url,
        offset: newOffset ?? prev.offset,
      }
      return
    }

    const currentUrl = focusIndex >= 0 ? filtered[focusIndex]?.url ?? null : null
    const currentOffset = focusIndex >= 0
      ? virtualizer.getOffsetForIndex(focusIndex, "start")?.[0] ?? null
      : null
    prevFocusSnapshot.current = currentUrl != null && currentOffset != null
      ? { filtered, index: focusIndex, url: currentUrl, offset: currentOffset }
      : null
  }, [filtered, focusIndex, virtualizer, selection, showUnreadOnly, timeRangeId])

  // Scroll focused article into view (custom rAF smooth scroll for tunable duration)
  useEffect(() => {
    if (focusIndex < 0) return
    if (skipFocusScroll.current) {
      skipFocusScroll.current = false
      return
    }
    const main = mainRef.current
    if (!main) return
    const el = articleRefs.current.get(focusIndex)
    // Out-of-range (virtualized away): jump with virtualizer and let the
    // next render place the card; the rAF smooth path below handles the
    // in-range case.
    if (!el) {
      virtualizer.scrollToIndex(focusIndex, { align: "start" })
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
      const headerBottom = stickyHeaderRef.current
        ? stickyHeaderRef.current.getBoundingClientRect().bottom
        : main.getBoundingClientRect().top
      target = main.scrollTop + el.getBoundingClientRect().top - headerBottom
    }
    const start = main.scrollTop
    const distance = target - start
    if (Math.abs(distance) < 1) return
    const duration = 150
    const t0 = performance.now()
    const step = (now: number) => {
      const p = Math.min(1, (now - t0) / duration)
      const eased = 1 - Math.pow(1 - p, 3) // ease-out cubic
      main.scrollTop = start + distance * eased
      if (p < 1) {
        focusScrollRafRef.current = requestAnimationFrame(step)
      } else {
        focusScrollRafRef.current = null
      }
    }
    focusScrollRafRef.current = requestAnimationFrame(step)
    return () => {
      if (focusScrollRafRef.current != null) {
        cancelAnimationFrame(focusScrollRafRef.current)
        focusScrollRafRef.current = null
      }
    }
  }, [focusIndex, virtualizer])

  // Mark article as read when focus leaves it
  const markFocusedAsRead = useCallback((index: number) => {
    if (index < 0 || index >= filtered.length) return
    const article = filtered[index]
    if (article && article.read_at == null) {
      scheduleRead(article.url)
    }
  }, [filtered, scheduleRead])

  // Find the next still-unread article after `after` in the current view.
  // Session-read items count as read so the user is not redirected to an
  // article they just dismissed via j/m.
  const nextUnreadInView = useCallback((after: number) => {
    for (let i = after + 1; i < filtered.length; i++) {
      const a = filtered[i]
      if (a.read_at == null && !sessionReadUrls.current.has(a.url)) return i
    }
    return -1
  }, [filtered])

  // Mark all as read mutation
  const markAllReadFeedId = selection.type === "feed" ? selection.id : undefined
  const markAllRead = useMutation({
    mutationFn: (olderThanHours: number | null) =>
      api.markAllRead(markAllReadFeedId, olderThanHours ?? undefined),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["articles"] })
      queryClient.invalidateQueries({ queryKey: ["feed-stats"] })
    },
  })
  const markAllUnread = useMutation({
    mutationFn: () => api.markAllUnread(markAllReadFeedId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["articles"] })
      queryClient.invalidateQueries({ queryKey: ["feed-stats"] })
    },
  })

  // Toggle read/unread for a single article
  const toggleReadMutation = useMutation({
    mutationFn: ({ url, isRead }: { url: string, isRead: boolean }) =>
      isRead ? api.markUnread([url]) : api.markRead([url]),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["articles"] })
      queryClient.invalidateQueries({ queryKey: ["feed-stats"] })
    },
    onError: (err) => {
      console.error("Failed to toggle read status:", err)
    },
  })
  const toggleRead = useCallback((url: string, isRead: boolean) => {
    toggleReadMutation.mutate({ url, isRead })
  }, [toggleReadMutation])

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
    const next = nextUnreadFeedId(orderedFeedIds, refFeedId, unreadCounts)
    if (next == null) return false
    updateSelection({ type: "feed", id: next })
    return true
  }, [selection, orderedFeedIds, unreadCounts, updateSelection, focusIndex, filtered])

  const onJAtEnd = useCallback(() => {
    // Cancel any in-flight focus-scroll rAF. Without this, the still-running
    // rAF keeps writing main.scrollTop each frame and silently overrides the
    // smooth scroll-to-bottom we issue below.
    if (focusScrollRafRef.current != null) {
      cancelAnimationFrame(focusScrollRafRef.current)
      focusScrollRafRef.current = null
    }
    setCaughtUpPulseKey((k) => k + 1)
    const main = mainRef.current
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
    const main = mainRef.current
    if (!main || focusIndex < 0) return false
    const el = articleRefs.current.get(focusIndex)
    // Virtualized away: the card isn't in the DOM, so it's definitely not
    // aligned. Jump to it with the virtualizer and stay on this index.
    if (!el) {
      virtualizer.scrollToIndex(focusIndex, { align: "start" })
      return true
    }
    const headerBottom = stickyHeaderRef.current
      ? stickyHeaderRef.current.getBoundingClientRect().bottom
      : main.getBoundingClientRect().top
    const cardTop = el.getBoundingClientRect().top
    if (cardTop >= headerBottom - 4) return false
    const target = focusIndex === 0
      ? 0
      : main.scrollTop + cardTop - headerBottom
    const start = main.scrollTop
    const distance = target - start
    if (Math.abs(distance) < 1) return true
    const duration = 150
    const t0 = performance.now()
    const step = (now: number) => {
      const p = Math.min(1, (now - t0) / duration)
      const eased = 1 - Math.pow(1 - p, 3)
      main.scrollTop = start + distance * eased
      if (p < 1) requestAnimationFrame(step)
    }
    requestAnimationFrame(step)
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
    setShowHelp,
  })

  const setRef = useCallback((index: number, el: HTMLDivElement | null) => {
    if (el) {
      articleRefs.current.set(index, el)
    } else {
      articleRefs.current.delete(index)
    }
  }, [])

  const handleCardClick = useCallback((index: number) => {
    setFocusIndex((prev) => {
      if (prev !== index) markFocusedAsRead(prev)
      return index
    })
  }, [markFocusedAsRead])

  const selectionHeader = useMemo(() => {
    if (selection.type === "feed") {
      const feed = feedMap.get(selection.id)
      if (!feed) return null
      return { icon: faviconUrl(feed), label: feed.name }
    }
    if (selection.type === "folder") {
      const label = folders?.find((folder) => folder.id === selection.id)?.name ?? null
      return label ? { icon: null, label } : null
    }
    return null
  }, [selection, feedMap, folders])

  // Unread count for the header `Unread (N)` button. `filtered` already
  // applied selection + (in unread mode) the unread filter, so iterate that
  // directly. Session-read items are kept in `filtered` (intentional, so the
  // user can still see what they just dismissed) — exclude them here so the
  // count reflects only true unreads.
  const selectedUnreadInView = useMemo(() => {
    void localReadCount
    let n = 0
    for (const a of filtered) {
      if (a.read_at == null && !sessionReadUrls.current.has(a.url)) n++
    }
    return n
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
        <main ref={mainRef} className="flex-1 overflow-y-auto px-4 pb-2 flex flex-col items-center">
          <div className="w-[95%] max-w-4xl pl-1">
          <div ref={stickySentinelRef} aria-hidden className="h-0" />
          <div
            ref={stickyHeaderRef}
            className="sticky top-0 z-10 bg-bg pt-4 mb-4"
          >
            {selectionHeader && (
              <h2
                className={`font-medium text-text-heading flex items-center gap-2 transition-[font-size,padding] duration-200 ease-out ${
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
              </h2>
            )}
            <div className="flex items-center justify-between pb-2">
              <div className="flex items-center gap-2 text-sm">
                <div className="flex gap-1">
                  <button
                    onClick={() => setShowUnreadOnly(true)}
                    className={`px-3 py-1 rounded transition-colors ${
                      showUnreadOnly
                        ? "bg-accent-bg text-accent-text"
                        : "bg-bg-card text-text-muted hover:text-text"
                    }`}
                  >
                    Unread{selectedUnreadInView > 0 ? ` (${selectedUnreadInView})` : ""}
                  </button>
                  <button
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
                  onChange={(e) => updateTimeRangeId(parseTimeRangeId(e.target.value))}
                  disabled={showUnreadOnly}
                  className="px-2 py-1 rounded transition-colors outline-none border border-transparent focus:border-border bg-bg-card text-text-muted disabled:opacity-40 disabled:cursor-not-allowed"
                  aria-label="Time range filter"
                >
                  {TIME_RANGE_OPTIONS.map((opt) => (
                    <option key={opt.id} value={opt.id}>{opt.label}</option>
                  ))}
                </select>
              </div>
              {filtered.length > 0 && (
                <MarkAllReadMenu
                  disabled={markAllRead.isPending || markAllUnread.isPending}
                  onChoose={(hours) => markAllRead.mutate(hours)}
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
            <>
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
                {virtualizer.getVirtualItems().map((vi) => {
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
                  const feed = article.feed_id != null ? feedMap.get(article.feed_id) : undefined
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
                        pendingSummary={!article.summary && !article.content_translated && article.feed_id != null && summarizeFeedIds.has(article.feed_id)}
                        feedName={feed?.name}
                        feedFaviconUrl={feed ? faviconUrl(feed) : null}
                        ref={(el) => setRef(vi.index, el)}
                        onClick={() => handleCardClick(vi.index)}
                      />
                    </div>
                  )
                })}
              </div>
            </>
          )}
          </div>
        </main>
      </div>

      {showHelp && <ShortcutsHelp onClose={() => setShowHelp(false)} />}
    </div>
  )
}
