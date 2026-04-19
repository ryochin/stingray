import { useState, useMemo, useEffect, useRef, useCallback } from "react"
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query"
import { api, faviconUrl } from "../api/client"
import type { Feed, Selection } from "../api/client"
import Header from "../components/Header"
import Sidebar from "../components/Sidebar"
import ArticleCard from "../components/ArticleCard"
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

  const { data: allArticles, isLoading, isError } = useQuery({
    queryKey: ["articles"],
    queryFn: () => api.getArticles(),
    refetchInterval: 15_000,
  })

  const { data: feeds } = useQuery({
    queryKey: ["feeds"],
    queryFn: api.getFeeds,
    refetchInterval: 15_000,
  })

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
  useEffect(() => {
    setFocusIndex(-1)
  }, [selection, showUnreadOnly])

  // Scroll focused article into view (custom rAF smooth scroll for tunable duration)
  useEffect(() => {
    if (focusIndex < 0) return
    const el = articleRefs.current.get(focusIndex)
    const main = mainRef.current
    if (!el || !main) return
    const target = main.scrollTop + el.getBoundingClientRect().top - main.getBoundingClientRect().top
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
    mutationFn: () => api.markAllRead(markAllReadFeedId),
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

  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return
    const tag = (e.target as HTMLElement).tagName
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return

    if (e.key === "?" || (e.key === "/" && e.shiftKey)) {
      e.preventDefault()
      setShowHelp((v) => !v)
      return
    }
    if (e.key === "Escape") {
      setShowHelp(false)
      return
    }

    const len = filtered.length

    if (e.key === "j") {
      if (len === 0) {
        e.preventDefault()
        goToNextFeed()
        return
      }
      e.preventDefault()
      setFocusIndex((prev) => {
        markFocusedAsRead(prev)
        if (prev >= len - 1) {
          goToNextFeed()
          return -1
        }
        return prev + 1
      })
    } else if (len === 0) {
      return
    } else if (e.key === "k") {
      e.preventDefault()
      setFocusIndex((prev) => {
        markFocusedAsRead(prev)
        return Math.max(prev - 1, 0)
      })
    } else if (e.key === "v" || e.key === "o" || e.key === "Enter") {
      e.preventDefault()
      setFocusIndex((i) => {
        if (i >= 0 && i < len) {
          const article = filtered[i]
          window.open(article.url, "_blank", "noopener")
          if (article.read_at == null) {
            scheduleRead(article.url)
          }
        }
        return i
      })
    } else if (e.key === "m") {
      e.preventDefault()
      setFocusIndex((i) => {
        if (i >= 0 && i < len) {
          const article = filtered[i]
          toggleRead(article.url, article.read_at != null)
        }
        return i
      })
    } else if (e.key === "A" && e.shiftKey) {
      e.preventDefault()
      markAllRead.mutate()
    }
  }, [filtered, markFocusedAsRead, scheduleRead, toggleRead, markAllRead, goToNextFeed])

  useEffect(() => {
    window.addEventListener("keydown", handleKeyDown)
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [handleKeyDown])

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
        <main ref={mainRef} className="flex-1 overflow-y-auto px-4 py-5 flex flex-col items-center">
          <div className="w-[95%] max-w-4xl pl-1">
          {selectionHeader && (
            <h2 className="text-2xl font-medium text-text-heading mb-3 flex items-center gap-2">
              {selectionHeader.icon && (
                <img src={selectionHeader.icon} alt="" className="w-5 h-5" loading="lazy" />
              )}
              {selectionHeader.label}
            </h2>
          )}
          <div className="flex items-center justify-between mb-4">
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
              <button
                onClick={() => markAllRead.mutate()}
                disabled={markAllRead.isPending}
                className="text-sm px-3 py-1 rounded bg-bg-card text-text-muted hover:text-text transition-colors disabled:opacity-40"
              >
                Mark all as read
              </button>
            )}
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
              <div className="flex items-center gap-3 py-6 text-text-dim text-sm">
                <div className="flex-1 border-t border-border" />
                <span>All caught up</span>
                <div className="flex-1 border-t border-border" />
              </div>
            </>
          )}
          </div>
        </main>
      </div>

      {showHelp && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={() => setShowHelp(false)}>
          <div className="bg-bg-secondary border border-border rounded-lg p-6 max-w-sm" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-text-heading font-semibold mb-4">Keyboard Shortcuts</h3>
            <table className="text-sm w-full">
              <tbody>
                {[
                  ["j", "Next article"],
                  ["k", "Previous article"],
                  ["v / o / Enter", "Open in new tab"],
                  ["m", "Toggle read/unread"],
                  ["Shift+A", "Mark all as read"],
                  ["?", "Show/hide this help"],
                  ["a", "Go to Articles"],
                  ["f", "Go to Feeds"],
                ].map(([key, desc]) => (
                  <tr key={key}>
                    <td className="pr-4 py-1"><kbd className="px-1.5 py-0.5 rounded bg-bg-card text-accent-text text-xs font-mono">{key}</kbd></td>
                    <td className="py-1 text-text-muted">{desc}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
