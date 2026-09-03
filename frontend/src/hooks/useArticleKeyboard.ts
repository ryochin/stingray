import {
  type Dispatch,
  type SetStateAction,
  useCallback,
  useEffect,
} from "react"
import type { Article } from "../api/client"
import { isModalOpen, isShortcutSuppressed } from "../utils/keyboardGuards"

interface Options {
  filtered: Article[]
  setFocusIndex: Dispatch<SetStateAction<number>>
  markFocusedAsRead: (index: number) => void
  canJumpToNextFeed: boolean
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
 *   Space — only intercepted while the caught-up hint advertising the
 *           Space shortcut is visible (`canJumpToNextFeed`). In that
 *           state, jump to the next feed that still has unread articles.
 *           Otherwise do not intercept — let the browser scroll naturally.
 *           This deliberately requires the user to surface the hint
 *           (two j-at-end presses) before Space takes effect, so an
 *           idle Space at the bottom of the list never silently
 *           navigates away.
 *   v/o/Enter — open focused article in a new tab
 *   m     — toggle read/unread on focused article
 *   Shift+A — mark all as read
 *   u     — toggle Unread / All filter
 *   ?     — show/hide help
 *   Esc   — close help
 *
 * Text entry is never intercepted. While the help overlay is open, the only
 * keys this hook still acts on are ? and Esc, which dismiss it; ShortcutsHelp
 * itself additionally owns Enter/Space/Escape while focused. See
 * `utils/keyboardGuards`.
 * `setFocusIndex` is the functional setter from `useState`; keeping the
 * functional-update form inside the hook makes focus advancement atomic with
 * the read-marking it triggers.
 */
export function useArticleKeyboard({
  filtered,
  setFocusIndex,
  markFocusedAsRead,
  canJumpToNextFeed,
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
      if (isShortcutSuppressed(e)) return

      // ? and Esc address the help overlay itself, so they run before the
      // modal check below — they are the way out of it.
      if (e.key === "?" || (e.key === "/" && e.shiftKey)) {
        e.preventDefault()
        setShowHelp((v: boolean): boolean => !v)
        return
      }
      if (e.key === "Escape") {
        setShowHelp(false)
        return
      }

      // Everything past this point drives the article list, which sits behind
      // the overlay while it is open.
      if (isModalOpen()) return

      if (e.key === " ") {
        // Leave Shift+Space (PageUp) and events another handler already
        // processed to their owners. Unlike Enter — which ArticleCard
        // preventDefaults on its way through and still expects to be opened
        // by this handler — a consumed Space is never ours.
        if (e.shiftKey || e.defaultPrevented) return
        // Respect focus on a native <button>: its browser-default Space
        // activation (Unread/All toggle, MarkAllReadMenu items, etc.) must
        // win over the feed-jump shortcut. role="button" divs are not
        // matched here on purpose — ArticleCard wants Space to fall through
        // to this handler.
        const targetEl = e.target as HTMLElement | null
        if (targetEl?.closest("button")) return
        // Only steal Space while the caught-up hint advertising the
        // shortcut is visible. In every other state — including merely
        // reaching the bottom of the list — let the browser perform its
        // default scroll. This prevents an accidental Space at rest from
        // silently navigating to another feed.
        if (!canJumpToNextFeed) return
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
      setFocusIndex,
      markFocusedAsRead,
      canJumpToNextFeed,
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
