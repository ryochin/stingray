import { useState, useMemo, useEffect, useRef, useCallback } from "react"
import { useQuery } from "@tanstack/react-query"
import { api } from "../api/client"
import Header from "../components/Header"
import Sidebar from "../components/Sidebar"
import ArticleCard from "../components/ArticleCard"

export default function Articles() {
  const [activeFeedId, setActiveFeedId] = useState<number | null>(null)
  const [focusIndex, setFocusIndex] = useState(-1)
  const articleRefs = useRef<Map<number, HTMLDivElement>>(new Map())
  const mainRef = useRef<HTMLElement>(null)

  const { data: allArticles, isLoading } = useQuery({
    queryKey: ["articles"],
    queryFn: () => api.getArticles(),
  })

  const articleCounts = useMemo(() => {
    const map = new Map<number, number>()
    for (const a of allArticles ?? []) {
      if (a.feed_id != null) {
        map.set(a.feed_id, (map.get(a.feed_id) ?? 0) + 1)
      }
    }
    return map
  }, [allArticles])

  const filtered = useMemo(() => {
    if (activeFeedId === null) return allArticles ?? []
    return (allArticles ?? []).filter((a) => a.feed_id === activeFeedId)
  }, [allArticles, activeFeedId])

  // Focus first article when feed changes or articles load
  useEffect(() => {
    setFocusIndex(filtered.length > 0 ? 0 : -1)
  }, [activeFeedId, filtered.length])

  // Scroll focused article into view
  useEffect(() => {
    if (focusIndex < 0) return
    const el = articleRefs.current.get(focusIndex)
    el?.scrollIntoView({ block: "start", behavior: "smooth" })
  }, [focusIndex])

  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    // Ignore when typing in inputs
    const tag = (e.target as HTMLElement).tagName
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return

    const len = filtered.length
    if (len === 0) return

    if (e.key === "j") {
      e.preventDefault()
      setFocusIndex((i) => Math.min(i + 1, len - 1))
    } else if (e.key === "k") {
      e.preventDefault()
      setFocusIndex((i) => Math.max(i - 1, 0))
    } else if (e.key === "o" || e.key === "Enter") {
      e.preventDefault()
      setFocusIndex((i) => {
        if (i >= 0 && i < len) {
          window.open(filtered[i].url, "_blank", "noopener")
        }
        return i
      })
    }
  }, [filtered])

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

  return (
    <div className="flex flex-col h-screen">
      <Header />
      <div className="flex flex-1 min-h-0">
        <Sidebar
          activeFeedId={activeFeedId}
          onSelect={setActiveFeedId}
          articleCounts={articleCounts}
        />
        <main ref={mainRef} className="flex-1 overflow-y-auto px-7 py-5">
          {isLoading ? (
            <div className="text-text-muted">Loading...</div>
          ) : filtered.length === 0 ? (
            <div className="text-text-muted">No articles</div>
          ) : (
            filtered.map((article, i) => (
              <ArticleCard
                key={article.url}
                article={article}
                focused={i === focusIndex}
                ref={(el) => setRef(i, el)}
                onClick={() => setFocusIndex(i)}
              />
            ))
          )}
        </main>
      </div>
    </div>
  )
}
