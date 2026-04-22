import { useCallback, useEffect, type Dispatch, type SetStateAction } from "react"
import type { Article } from "../api/client"

interface Options {
  filtered: Article[]
  setFocusIndex: Dispatch<SetStateAction<number>>
  markFocusedAsRead: (index: number) => void
  scheduleRead: (url: string) => void
  toggleRead: (url: string, isRead: boolean) => void
  markAllRead: () => void
  goToNextFeed: () => void
  onJAtEnd: () => void
  setShowHelp: Dispatch<SetStateAction<boolean>>
}

/**
 * Global keyboard shortcuts for the Articles view:
 *   j/k   — next/prev article (marks the previous one as read)
 *   Space — jump to next feed that still has unread articles
 *   v/o/Enter — open focused article in a new tab
 *   m     — toggle read/unread on focused article
 *   Shift+A — mark all as read
 *   ?     — show/hide help
 *   Esc   — close help
 *
 * Typing inside input/textarea/select is never intercepted. `setFocusIndex` is
 * the functional setter from `useState`; keeping the functional-update form
 * inside the hook makes focus advancement atomic with the read-marking it
 * triggers.
 */
export function useArticleKeyboard({
  filtered, setFocusIndex, markFocusedAsRead, scheduleRead, toggleRead,
  markAllRead, goToNextFeed, onJAtEnd, setShowHelp,
}: Options) {
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

    if (e.key === " ") {
      e.preventDefault()
      setFocusIndex((prev) => {
        markFocusedAsRead(prev)
        goToNextFeed()
        return -1
      })
      return
    }

    const len = filtered.length

    if (e.key === "j") {
      if (len === 0) return
      e.preventDefault()
      setFocusIndex((prev) => {
        markFocusedAsRead(prev)
        if (prev === len - 1) {
          onJAtEnd()
          return prev
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
          if (article.read_at == null) scheduleRead(article.url)
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
      markAllRead()
    }
  }, [
    filtered, setFocusIndex, markFocusedAsRead, scheduleRead, toggleRead,
    markAllRead, goToNextFeed, onJAtEnd, setShowHelp,
  ])

  useEffect(() => {
    window.addEventListener("keydown", handleKeyDown)
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [handleKeyDown])
}
