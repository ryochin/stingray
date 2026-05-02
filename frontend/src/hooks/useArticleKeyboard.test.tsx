import { describe, it, expect, vi, afterEach } from "vitest"
import type { Mock } from "vitest"
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
  const event: KeyboardEvent = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true })
  act((): void => {
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
  onJAtEnd?: () => void
  toggleUnreadFilter?: () => void
}


function Harness({
  filtered, initialFocus, sessionRead = new Set(), goToNextFeed,
  onFocus, onMarkRead, onJAtEnd, toggleUnreadFilter,
}: HarnessProps): null {
  const [focusIndex, setFocusIndex] = useState<number>(initialFocus)
  onFocus?.(focusIndex)

  const markFocusedAsRead = (i: number): void => {
    if (i < 0 || i >= filtered.length) return
    onMarkRead?.(i)
  }
  const nextUnreadInView = (after: number): number => {
    for (let i: number = after + 1; i < filtered.length; i++) {
      const article: Article = filtered[i]
      if (article.read_at == null && !sessionRead.has(article.url)) return i
    }
    return -1
  }

  useArticleKeyboard({
    filtered,
    focusIndex,
    setFocusIndex,
    markFocusedAsRead,
    nextUnreadInView,
    scheduleRead: (): void => {},
    toggleRead: (): void => {},
    markAllRead: (): void => {},
    goToNextFeed,
    onJAtEnd: onJAtEnd ?? ((): void => {}),
    onKBeforeMove: (): boolean => false,
    toggleUnreadFilter: toggleUnreadFilter ?? ((): void => {}),
    setShowHelp: (): void => {},
  })
  return null
}


afterEach((): void => {
  document.body.innerHTML = ""
})


describe("useArticleKeyboard — Space key (regression: feed jump while unread remain)", (): void => {
  it("does NOT intercept Space when unread articles remain after focus — browser scroll wins", (): void => {
    const articles: Article[] = [
      makeArticle({ url: "a", read_at: null }),
      makeArticle({ url: "b", read_at: null }),
      makeArticle({ url: "c", read_at: null }),
    ]
    const focusSpy: Mock = vi.fn()
    const markSpy: Mock = vi.fn()
    const nextFeed: Mock = vi.fn((): boolean => true)
    render(
      <Harness
        filtered={articles}
        initialFocus={0}
        goToNextFeed={nextFeed}
        onFocus={focusSpy}
        onMarkRead={markSpy}
      />,
    )

    const event: KeyboardEvent = press(" ")

    expect(event.defaultPrevented).toBe(false)
    expect(nextFeed).not.toHaveBeenCalled()
    expect(markSpy).not.toHaveBeenCalled()
    // Focus stays put — the harness only ever reported the initial value.
    expect(focusSpy).toHaveBeenLastCalledWith(0)
  })

  it("when focus is on the last unread, jumps to the next feed", (): void => {
    const articles: Article[] = [
      makeArticle({ url: "a", read_at: "2024-01-01T00:00:00Z" }),
      makeArticle({ url: "b", read_at: null }), // last unread
    ]
    const focusSpy: Mock = vi.fn()
    const markSpy: Mock = vi.fn()
    const nextFeed: Mock = vi.fn((): boolean => true)
    render(
      <Harness
        filtered={articles}
        initialFocus={1}
        goToNextFeed={nextFeed}
        onFocus={focusSpy}
        onMarkRead={markSpy}
      />,
    )

    const event: KeyboardEvent = press(" ")

    expect(event.defaultPrevented).toBe(true)
    expect(nextFeed).toHaveBeenCalledTimes(1)
    expect(markSpy).toHaveBeenCalledWith(1)
    // Focus is cleared (-1) after a successful feed jump.
    expect(focusSpy).toHaveBeenLastCalledWith(-1)
  })

  it("if goToNextFeed reports no further feed, focus stays put", (): void => {
    const articles: Article[] = [
      makeArticle({ url: "a", read_at: null }),
    ]
    const focusSpy: Mock = vi.fn()
    const nextFeed: Mock = vi.fn((): boolean => false)
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

  it("session-read items count as read when deciding whether to jump feeds", (): void => {
    // a is the focus; b is session-read; c is the only true unread remaining.
    // Because c is unread, Space must NOT be intercepted.
    const articles: Article[] = [
      makeArticle({ url: "a", read_at: null }),
      makeArticle({ url: "b", read_at: null }),
      makeArticle({ url: "c", read_at: null }),
    ]
    const focusSpy: Mock = vi.fn()
    const nextFeed: Mock = vi.fn((): boolean => true)
    const { rerender } = render(
      <Harness
        filtered={articles}
        initialFocus={0}
        sessionRead={new Set(["b"])}
        goToNextFeed={nextFeed}
        onFocus={focusSpy}
      />,
    )

    let event: KeyboardEvent = press(" ")
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

  it("with no unread anywhere after focus, jumps to next feed", (): void => {
    const articles: Article[] = [
      makeArticle({ url: "a", read_at: null }),
      makeArticle({ url: "b", read_at: "2024-01-01T00:00:00Z" }),
      makeArticle({ url: "c", read_at: "2024-01-01T00:00:00Z" }),
    ]
    const focusSpy: Mock = vi.fn()
    const nextFeed: Mock = vi.fn((): boolean => true)
    render(
      <Harness
        filtered={articles}
        initialFocus={0}
        goToNextFeed={nextFeed}
        onFocus={focusSpy}
      />,
    )

    const event: KeyboardEvent = press(" ")

    expect(event.defaultPrevented).toBe(true)
    expect(nextFeed).toHaveBeenCalledTimes(1)
    expect(focusSpy).toHaveBeenLastCalledWith(-1)
  })
})


describe("useArticleKeyboard — u key (toggle Unread/All)", (): void => {
  it("invokes toggleUnreadFilter when pressed", () => {
    const toggle: Mock = vi.fn()
    render(
      <Harness
        filtered={[]}
        initialFocus={-1}
        goToNextFeed={(): boolean => false}
        toggleUnreadFilter={toggle}
      />,
    )

    const event: KeyboardEvent = press("u")

    expect(event.defaultPrevented).toBe(true)
    expect(toggle).toHaveBeenCalledTimes(1)
  })

  it("works even when the filtered list is non-empty (does not consume focus)", () => {
    const toggle: Mock = vi.fn()
    const focusSpy: Mock = vi.fn()
    render(
      <Harness
        filtered={[makeArticle({ url: "a", read_at: null })]}
        initialFocus={0}
        goToNextFeed={(): boolean => false}
        onFocus={focusSpy}
        toggleUnreadFilter={toggle}
      />,
    )

    press("u")

    expect(toggle).toHaveBeenCalledTimes(1)
    expect(focusSpy).toHaveBeenLastCalledWith(0)
  })

  it("is suppressed when modifier keys are held", () => {
    const toggle: Mock = vi.fn()
    render(
      <Harness
        filtered={[]}
        initialFocus={-1}
        goToNextFeed={(): boolean => false}
        toggleUnreadFilter={toggle}
      />,
    )

    const event: KeyboardEvent = new KeyboardEvent("keydown", {
      key: "u", bubbles: true, cancelable: true, metaKey: true,
    })
    act(() => {
      document.body.dispatchEvent(event)
    })

    expect(toggle).not.toHaveBeenCalled()
    expect(event.defaultPrevented).toBe(false)
  })

  it("is suppressed while typing inside <input>", () => {
    const toggle: Mock = vi.fn()
    render(
      <Harness
        filtered={[]}
        initialFocus={-1}
        goToNextFeed={(): boolean => false}
        toggleUnreadFilter={toggle}
      />,
    )

    const input: HTMLInputElement = document.createElement("input")
    document.body.appendChild(input)
    const event: KeyboardEvent = new KeyboardEvent("keydown", {
      key: "u", bubbles: true, cancelable: true,
    })
    act(() => {
      input.dispatchEvent(event)
    })

    expect(toggle).not.toHaveBeenCalled()
  })
})


describe("useArticleKeyboard — j key at end of list", (): void => {
  it("on the last item, fires onJAtEnd, keeps focus, does NOT jump feeds", (): void => {
    const articles: Article[] = [
      makeArticle({ url: "a", read_at: null }),
      makeArticle({ url: "b", read_at: null }),
      makeArticle({ url: "c", read_at: null }),
    ]
    const focusSpy: Mock = vi.fn()
    const markSpy: Mock = vi.fn()
    const nextFeed: Mock = vi.fn((): boolean => true)
    const jAtEnd: Mock = vi.fn()
    render(
      <Harness
        filtered={articles}
        initialFocus={2}
        goToNextFeed={nextFeed}
        onFocus={focusSpy}
        onMarkRead={markSpy}
        onJAtEnd={jAtEnd}
      />,
    )

    press("j")

    expect(jAtEnd).toHaveBeenCalledTimes(1)
    expect(nextFeed).not.toHaveBeenCalled()
    expect(markSpy).toHaveBeenCalledWith(2)
    // Focus stays on the last item.
    expect(focusSpy).toHaveBeenLastCalledWith(2)
  })

  it("not on the last item, advances focus and does NOT fire onJAtEnd", (): void => {
    const articles: Article[] = [
      makeArticle({ url: "a", read_at: null }),
      makeArticle({ url: "b", read_at: null }),
    ]
    const focusSpy: Mock = vi.fn()
    const jAtEnd: Mock = vi.fn()
    const nextFeed: Mock = vi.fn((): boolean => true)
    render(
      <Harness
        filtered={articles}
        initialFocus={0}
        goToNextFeed={nextFeed}
        onFocus={focusSpy}
        onJAtEnd={jAtEnd}
      />,
    )

    press("j")

    expect(jAtEnd).not.toHaveBeenCalled()
    expect(nextFeed).not.toHaveBeenCalled()
    expect(focusSpy).toHaveBeenLastCalledWith(1)
  })
})
