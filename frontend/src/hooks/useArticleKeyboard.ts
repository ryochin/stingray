import {
  type Dispatch,
  type SetStateAction,
  useCallback,
  useEffect,
} from "react"
import type { Article } from "../api/client"

interface Options {
  filtered: Article[]
  focusIndex: number
  setFocusIndex: Dispatch<SetStateAction<number>>
  markFocusedAsRead: (index: number) => void
  nextUnreadInView: (after: number) => number
  scheduleRead: (url: string) => void
  toggleRead: (url: string, isRead: boolean) => void
  markAllRead: () => void
  goToNextFeed: () => boolean
  onJAtEnd: () => void
  onKBeforeMove: () => boolean
  toggleUnreadFilter: () => void
  setShowHelp: Dispatch<SetStateAction<boolean>>
}

/**
 * Global keyboard shortcuts for the Articles view:
 *   j/k   — next/prev article (marks the previous one as read)
 *   Space — when there are no more unread articles after focus in this view,
 *           jump to the next feed that still has unread articles. Otherwise
 *           do not intercept — let the browser scroll naturally.
 *   v/o/Enter — open focused article in a new tab
 *   m     — toggle read/unread on focused article
 *   Shift+A — mark all as read
 *   u     — toggle Unread / All filter
 *   ?     — show/hide help
 *   Esc   — close help
 *
 * Typing inside input/textarea/select is never intercepted. `setFocusIndex` is
 * the functional setter from `useState`; keeping the functional-update form
 * inside the hook makes focus advancement atomic with the read-marking it
 * triggers.
 */
export function useArticleKeyboard({
  filtered,
  focusIndex,
  setFocusIndex,
  markFocusedAsRead,
  nextUnreadInView,
  scheduleRead,
  toggleRead,
  markAllRead,
  goToNextFeed,
  onJAtEnd,
  onKBeforeMove,
  toggleUnreadFilter,
  setShowHelp,
}: Options): void {
  const handleKeyDown = useCallback(
    (e: KeyboardEvent): void => {
      if (e.metaKey || e.ctrlKey || e.altKey) return
      const tag: string = (e.target as HTMLElement).tagName
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return

      if (e.key === "?" || (e.key === "/" && e.shiftKey)) {
        e.preventDefault()
        setShowHelp((v: boolean): boolean => !v)
        return
      }
      if (e.key === "Escape") {
        setShowHelp(false)
        return
      }

      if (e.key === " ") {
        // Only steal Space when the user has finished the unread items in this
        // view. While unread items still remain, leave the keystroke alone so
        // the browser performs its default action (scroll the page down).
        if (nextUnreadInView(focusIndex) >= 0) return
        e.preventDefault()
        // Call goToNextFeed OUTSIDE the setFocusIndex updater so its side
        // effect (setSelection) fires exactly once even under Strict Mode,
        // and so React can batch it atomically with the focus change below.
        const moved: boolean = goToNextFeed()
        setFocusIndex((prev: number): number => {
          markFocusedAsRead(prev)
          // Only clear focus if the jump succeeded; otherwise stay put so
          // the auto-focus effect doesn't yank the user back to index 0.
          return moved ? -1 : prev
        })
        return
      }

      if (e.key === "u") {
        // Toggle Unread / All. Independent of list contents — toggling while
        // the view is empty is still meaningful (e.g. switching from Unread to
        // All to see what was just marked read).
        e.preventDefault()
        toggleUnreadFilter()
        return
      }

      const len: number = filtered.length

      if (e.key === "j") {
        if (len === 0) return
        e.preventDefault()
        setFocusIndex((prev: number): number => {
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
        if (onKBeforeMove()) return
        // k is "go back" — do NOT mark the article we're leaving as read.
        // j is the forward/read motion; k should stay purely navigational so
        // users can re-visit an article they just skimmed past without having
        // their focus target silently marked behind them.
        setFocusIndex((prev: number): number => Math.max(prev - 1, 0))
      } else if (e.key === "v" || e.key === "o" || e.key === "Enter") {
        e.preventDefault()
        setFocusIndex((i: number): number => {
          if (i >= 0 && i < len) {
            const article: Article = filtered[i]
            window.open(article.url, "_blank", "noopener")
            if (article.read_at == null) scheduleRead(article.url)
          }
          return i
        })
      } else if (e.key === "m") {
        e.preventDefault()
        setFocusIndex((i: number): number => {
          if (i >= 0 && i < len) {
            const article: Article = filtered[i]
            toggleRead(article.url, article.read_at != null)
          }
          return i
        })
      } else if (e.key === "A" && e.shiftKey) {
        e.preventDefault()
        markAllRead()
      }
    },
    [
      filtered,
      focusIndex,
      setFocusIndex,
      markFocusedAsRead,
      nextUnreadInView,
      scheduleRead,
      toggleRead,
      markAllRead,
      goToNextFeed,
      onJAtEnd,
      onKBeforeMove,
      toggleUnreadFilter,
      setShowHelp,
    ],
  )

  useEffect((): (() => void) => {
    window.addEventListener("keydown", handleKeyDown)
    return (): void => window.removeEventListener("keydown", handleKeyDown)
  }, [handleKeyDown])
}
