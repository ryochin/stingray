import { describe, it, expect, vi, afterEach } from "vitest"
import { render, act } from "@testing-library/react"
import { useState } from "react"
import { useArticleKeyboard } from "./useArticleKeyboard"
import type { Article } from "../api/client"


function makeArticle(overrides: Partial<Article> & { url: string }): Article {
  return {
    feed_id: 1,
    title: "t",
    title_translated: null,
    source: "s",
    published: null,
    content_snippet: null,
    summary: null,
    content_html: null,
    content_translated: null,
    read_at: null,
    ...overrides,
  }
}


function press(key: string): KeyboardEvent {
  const event = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true })
  act(() => {
    document.body.dispatchEvent(event)
  })
  return event
}


// Drive the hook from a harness that exposes focusIndex via a closure-bound
// state so we can assert focus changes after key presses.
interface HarnessProps {
  filtered: Article[]
  initialFocus: number
  sessionRead?: ReadonlySet<string>
  goToNextFeed: () => boolean
  onFocus?: (i: number) => void
  onMarkRead?: (i: number) => void
}


function Harness({
  filtered, initialFocus, sessionRead = new Set(), goToNextFeed,
  onFocus, onMarkRead,
}: HarnessProps) {
  const [focusIndex, setFocusIndex] = useState(initialFocus)
  onFocus?.(focusIndex)

  const markFocusedAsRead = (i: number) => {
    if (i < 0 || i >= filtered.length) return
    onMarkRead?.(i)
  }
  const nextUnreadInView = (after: number) => {
    for (let i = after + 1; i < filtered.length; i++) {
      const a = filtered[i]
      if (a.read_at == null && !sessionRead.has(a.url)) return i
    }
    return -1
  }

  useArticleKeyboard({
    filtered,
    focusIndex,
    setFocusIndex,
    markFocusedAsRead,
    nextUnreadInView,
    scheduleRead: () => {},
    toggleRead: () => {},
    markAllRead: () => {},
    goToNextFeed,
    onJAtEnd: () => {},
    onKBeforeMove: () => false,
    setShowHelp: () => {},
  })
  return null
}


afterEach(() => {
  document.body.innerHTML = ""
})


describe("useArticleKeyboard — Space key (regression: feed jump while unread remain)", () => {
  it("does NOT intercept Space when unread articles remain after focus — browser scroll wins", () => {
    const articles = [
      makeArticle({ url: "a", read_at: null }),
      makeArticle({ url: "b", read_at: null }),
      makeArticle({ url: "c", read_at: null }),
    ]
    const focusSpy = vi.fn()
    const markSpy = vi.fn()
    const nextFeed = vi.fn(() => true)
    render(
      <Harness
        filtered={articles}
        initialFocus={0}
        goToNextFeed={nextFeed}
        onFocus={focusSpy}
        onMarkRead={markSpy}
      />,
    )

    const event = press(" ")

    expect(event.defaultPrevented).toBe(false)
    expect(nextFeed).not.toHaveBeenCalled()
    expect(markSpy).not.toHaveBeenCalled()
    // Focus stays put — the harness only ever reported the initial value.
    expect(focusSpy).toHaveBeenLastCalledWith(0)
  })

  it("when focus is on the last unread, jumps to the next feed", () => {
    const articles = [
      makeArticle({ url: "a", read_at: "2024-01-01T00:00:00Z" }),
      makeArticle({ url: "b", read_at: null }), // last unread
    ]
    const focusSpy = vi.fn()
    const markSpy = vi.fn()
    const nextFeed = vi.fn(() => true)
    render(
      <Harness
        filtered={articles}
        initialFocus={1}
        goToNextFeed={nextFeed}
        onFocus={focusSpy}
        onMarkRead={markSpy}
      />,
    )

    const event = press(" ")

    expect(event.defaultPrevented).toBe(true)
    expect(nextFeed).toHaveBeenCalledTimes(1)
    expect(markSpy).toHaveBeenCalledWith(1)
    // Focus is cleared (-1) after a successful feed jump.
    expect(focusSpy).toHaveBeenLastCalledWith(-1)
  })

  it("if goToNextFeed reports no further feed, focus stays put", () => {
    const articles = [
      makeArticle({ url: "a", read_at: null }),
    ]
    const focusSpy = vi.fn()
    const nextFeed = vi.fn(() => false)
    render(
      <Harness
        filtered={articles}
        initialFocus={0}
        goToNextFeed={nextFeed}
        onFocus={focusSpy}
      />,
    )

    press(" ")

    expect(nextFeed).toHaveBeenCalledTimes(1)
    expect(focusSpy).toHaveBeenLastCalledWith(0)
  })

  it("session-read items count as read when deciding whether to jump feeds", () => {
    // a is the focus; b is session-read; c is the only true unread remaining.
    // Because c is unread, Space must NOT be intercepted.
    const articles = [
      makeArticle({ url: "a", read_at: null }),
      makeArticle({ url: "b", read_at: null }),
      makeArticle({ url: "c", read_at: null }),
    ]
    const focusSpy = vi.fn()
    const nextFeed = vi.fn(() => true)
    const { rerender } = render(
      <Harness
        filtered={articles}
        initialFocus={0}
        sessionRead={new Set(["b"])}
        goToNextFeed={nextFeed}
        onFocus={focusSpy}
      />,
    )

    let event = press(" ")
    expect(event.defaultPrevented).toBe(false)
    expect(nextFeed).not.toHaveBeenCalled()

    // Now mark c session-read too — no real unread left after focus → jump.
    rerender(
      <Harness
        filtered={articles}
        initialFocus={0}
        sessionRead={new Set(["b", "c"])}
        goToNextFeed={nextFeed}
        onFocus={focusSpy}
      />,
    )
    event = press(" ")
    expect(event.defaultPrevented).toBe(true)
    expect(nextFeed).toHaveBeenCalledTimes(1)
  })

  it("with no unread anywhere after focus, jumps to next feed", () => {
    const articles = [
      makeArticle({ url: "a", read_at: null }),
      makeArticle({ url: "b", read_at: "2024-01-01T00:00:00Z" }),
      makeArticle({ url: "c", read_at: "2024-01-01T00:00:00Z" }),
    ]
    const focusSpy = vi.fn()
    const nextFeed = vi.fn(() => true)
    render(
      <Harness
        filtered={articles}
        initialFocus={0}
        goToNextFeed={nextFeed}
        onFocus={focusSpy}
      />,
    )

    const event = press(" ")

    expect(event.defaultPrevented).toBe(true)
    expect(nextFeed).toHaveBeenCalledTimes(1)
    expect(focusSpy).toHaveBeenLastCalledWith(-1)
  })
})
