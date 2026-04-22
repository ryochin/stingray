import { useState, useMemo, useEffect, useRef, useCallback } from "react"
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query"
import { api, faviconUrl } from "../api/client"
import type { Feed, Selection } from "../api/client"
import Header from "../components/Header"
import Sidebar from "../components/Sidebar"
import ArticleCard from "../components/ArticleCard"
import MarkAllReadMenu from "../components/MarkAllReadMenu"
import ShortcutsHelp from "../components/ShortcutsHelp"
import { useArticleKeyboard } from "../hooks/useArticleKeyboard"
import {
  applyUnreadFilter,
  computeFolderFeedOrder,
  nextUnreadFeedId,
  selectArticles,
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

  // Debounced batch read marking
  const pendingReadUrls = useRef<Set<string>>(new Set())
  const flushTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const flushReads = useCallback(() => {
    if (pendingReadUrls.current.size === 0) return
    const urls = Array.from(pendingReadUrls.current)
    pendingReadUrls.current.clear()
    api.markRead(urls).then(() => {
      queryClient.invalidateQueries({ queryKey: ["articles"] })
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

  const { data: allArticles, isLoading, isError } = useQuery({
    queryKey: ["articles"],
    queryFn: () => api.getArticles(),
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
    }
    prevRunning.current = running
  }, [running, queryClient])

  const { data: folders } = useQuery({
    queryKey: ["folders"],
    queryFn: api.getFolders,
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

  // Unread counts (always from full list, not filtered)
  const unreadCounts = useMemo(() => {
    void localReadCount // trigger recomputation on local reads
    const map = new Map<number, number>()
    for (const a of enabledArticles) {
      if (a.feed_id != null && a.read_at == null && !sessionReadUrls.current.has(a.url)) {
        map.set(a.feed_id, (map.get(a.feed_id) ?? 0) + 1)
      }
    }
    return map
  }, [enabledArticles, localReadCount])

  const folderFeedOrder = useMemo(
    () => computeFolderFeedOrder(selection, feeds),
    [selection, feeds],
  )

  const filtered = useMemo(() => {
    void localReadCount // sessionReadUrls mutations are opaque to React; re-run when they tick.
    const selected = selectArticles(enabledArticles, selection, folderFeedOrder)
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

  // Clear session reads and reset focus when selection changes
  useEffect(() => {
    sessionReadUrls.current.clear()
  }, [selection])

  // Clear focus when feed/filter changes; the first j/k press will focus the first article.
  // Also reset scroll to the top so switching feeds (e.g. via j on the last card) always
  // lands the reader at the header, not wherever the previous feed was scrolled to.
  useEffect(() => {
    setFocusIndex(-1)
    mainRef.current?.scrollTo({ top: 0 })
  }, [selection, showUnreadOnly])

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

  // Scroll focused article into view (custom rAF smooth scroll for tunable duration)
  useEffect(() => {
    if (focusIndex < 0) return
    const el = articleRefs.current.get(focusIndex)
    const main = mainRef.current
    if (!el || !main) return
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
    let raf = 0
    const step = (now: number) => {
      const p = Math.min(1, (now - t0) / duration)
      const eased = 1 - Math.pow(1 - p, 3) // ease-out cubic
      main.scrollTop = start + distance * eased
      if (p < 1) raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [focusIndex])

  // Mark article as read when focus leaves it
  const markFocusedAsRead = useCallback((index: number) => {
    if (index < 0 || index >= filtered.length) return
    const article = filtered[index]
    if (article && article.read_at == null) {
      scheduleRead(article.url)
    }
  }, [filtered, scheduleRead])

  // Mark all as read mutation
  const markAllReadFeedId = selection.type === "feed" ? selection.id : undefined
  const markAllRead = useMutation({
    mutationFn: (olderThanHours: number | null) =>
      api.markAllRead(markAllReadFeedId, olderThanHours ?? undefined),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["articles"] })
    },
  })

  // Toggle read/unread for a single article
  const toggleReadMutation = useMutation({
    mutationFn: ({ url, isRead }: { url: string, isRead: boolean }) =>
      isRead ? api.markUnread([url]) : api.markRead([url]),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["articles"] })
    },
    onError: (err) => {
      console.error("Failed to toggle read status:", err)
    },
  })
  const toggleRead = useCallback((url: string, isRead: boolean) => {
    toggleReadMutation.mutate({ url, isRead })
  }, [toggleReadMutation])

  const goToNextFeed = useCallback(() => {
    if (selection.type !== "feed") return
    const next = nextUnreadFeedId(orderedFeedIds, selection.id, unreadCounts)
    if (next != null) updateSelection({ type: "feed", id: next })
  }, [selection, orderedFeedIds, unreadCounts, updateSelection])

  const onJAtEnd = useCallback(() => {
    setCaughtUpPulseKey((k) => k + 1)
    const main = mainRef.current
    if (main) main.scrollTo({ top: main.scrollHeight, behavior: "smooth" })
  }, [])

  useArticleKeyboard({
    filtered,
    setFocusIndex,
    markFocusedAsRead,
    scheduleRead,
    toggleRead,
    markAllRead: () => markAllRead.mutate(null),
    goToNextFeed,
    onJAtEnd,
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

  const selectedUnread = useMemo(() => {
    if (selection.type === "feed") return unreadCounts.get(selection.id) ?? 0
    if (selection.type === "folder" && folderFeedOrder) {
      let sum = 0
      for (const feedId of folderFeedOrder.keys()) sum += unreadCounts.get(feedId) ?? 0
      return sum
    }
    return Array.from(unreadCounts.values()).reduce((a, b) => a + b, 0)
  }, [selection, unreadCounts, folderFeedOrder])

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
              <div className="flex gap-1 text-sm">
                <button
                  onClick={() => setShowUnreadOnly(true)}
                  className={`px-3 py-1 rounded transition-colors ${
                    showUnreadOnly
                      ? "bg-accent-bg text-accent-text"
                      : "bg-bg-card text-text-muted hover:text-text"
                  }`}
                >
                  Unread{selectedUnread > 0 ? ` (${selectedUnread})` : ""}
                </button>
                <button
                  onClick={() => setShowUnreadOnly(false)}
                  className={`px-3 py-1 rounded transition-colors ${
                    !showUnreadOnly
                      ? "bg-accent-bg text-accent-text"
                      : "bg-bg-card text-text-muted hover:text-text"
                  }`}
                >
                  All
                </button>
              </div>
              {filtered.length > 0 && (
                <MarkAllReadMenu
                  disabled={markAllRead.isPending}
                  onChoose={(hours) => markAllRead.mutate(hours)}
                />
              )}
            </div>
          </div>

          {isError ? (
            <div className="text-red-400">Failed to load articles.</div>
          ) : isLoading ? (
            <div className="text-text-muted">Loading...</div>
          ) : filtered.length === 0 ? (
            <div className="text-text-muted">
              {showUnreadOnly ? "No unread articles" : "No articles"}
            </div>
          ) : (
            <>
              {filtered.map((article, index) => {
                const feed = article.feed_id != null ? feedMap.get(article.feed_id) : undefined
                return (
                  <ArticleCard
                    key={article.url}
                    article={article}
                    focused={index === focusIndex}
                    pendingSummary={!article.summary && !article.content_translated && article.feed_id != null && summarizeFeedIds.has(article.feed_id)}
                    feedName={feed?.name}
                    feedFaviconUrl={feed ? faviconUrl(feed) : null}
                    ref={(el) => setRef(index, el)}
                    onClick={() => handleCardClick(index)}
                  />
                )
              })}
              <div
                key={caughtUpPulseKey}
                className={`flex flex-col items-center gap-2 py-10 text-text-dim origin-center ${
                  caughtUpPulseKey > 0 ? "animate-caught-up-pulse" : ""
                }`}
              >
                <svg
                  className="w-7 h-7 text-accent-text/70"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.75"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden
                >
                  <path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z" />
                  <path d="M5 3v4" />
                  <path d="M19 17v4" />
                  <path d="M3 5h4" />
                  <path d="M17 19h4" />
                </svg>
                <span className="text-sm">All caught up</span>
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
