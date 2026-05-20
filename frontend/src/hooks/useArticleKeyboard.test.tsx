import { act, render } from "@testing-library/react"
import { useState } from "react"
import type { Mock } from "vitest"
import { afterEach, describe, expect, it, vi } from "vitest"
import type { Article } from "../api/client"
import { nextUnreadFeedId } from "../utils/articleView"
import { useArticleKeyboard } from "./useArticleKeyboard"

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
  const event: KeyboardEvent = new KeyboardEvent("keydown", {
    key,
    bubbles: true,
    cancelable: true,
  })
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
  canJumpToNextFeed?: boolean
  goToNextFeed: () => boolean
  onFocus?: (i: number) => void
  onMarkRead?: (i: number) => void
  onJAtEnd?: () => void
  toggleUnreadFilter?: () => void
}

function Harness({
  filtered,
  initialFocus,
  canJumpToNextFeed = false,
  goToNextFeed,
  onFocus,
  onMarkRead,
  onJAtEnd,
  toggleUnreadFilter,
}: HarnessProps): null {
  const [focusIndex, setFocusIndex] = useState<number>(initialFocus)
  onFocus?.(focusIndex)

  const markFocusedAsRead = (i: number): void => {
    if (i < 0 || i >= filtered.length) return
    onMarkRead?.(i)
  }

  useArticleKeyboard({
    filtered,
    setFocusIndex,
    markFocusedAsRead,
    canJumpToNextFeed,
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

describe("useArticleKeyboard — Space key (gated by caught-up hint)", (): void => {
  it("does NOT intercept Space while the hint is hidden — browser scroll wins", (): void => {
    // Even if there are no unread items left after focus, Space stays inert
    // until the caught-up hint advertising the shortcut is visible. This
    // guards against an idle Space at the bottom silently jumping feeds.
    const articles: Article[] = [
      makeArticle({ url: "a", read_at: null }),
      makeArticle({ url: "b", read_at: "2024-01-01T00:00:00Z" }),
    ]
    const focusSpy: Mock = vi.fn()
    const markSpy: Mock = vi.fn()
    const nextFeed: Mock = vi.fn((): boolean => true)
    render(
      <Harness
        filtered={articles}
        initialFocus={1}
        canJumpToNextFeed={false}
        goToNextFeed={nextFeed}
        onFocus={focusSpy}
        onMarkRead={markSpy}
      />,
    )

    const event: KeyboardEvent = press(" ")

    expect(event.defaultPrevented).toBe(false)
    expect(nextFeed).not.toHaveBeenCalled()
    expect(markSpy).not.toHaveBeenCalled()
    expect(focusSpy).toHaveBeenLastCalledWith(1)
  })

  it("jumps to the next feed when the hint flag is enabled", (): void => {
    const articles: Article[] = [
      makeArticle({ url: "a", read_at: "2024-01-01T00:00:00Z" }),
      makeArticle({ url: "b", read_at: null }),
    ]
    const focusSpy: Mock = vi.fn()
    const markSpy: Mock = vi.fn()
    const nextFeed: Mock = vi.fn((): boolean => true)
    render(
      <Harness
        filtered={articles}
        initialFocus={1}
        canJumpToNextFeed={true}
        goToNextFeed={nextFeed}
        onFocus={focusSpy}
        onMarkRead={markSpy}
      />,
    )

    const event: KeyboardEvent = press(" ")

    expect(event.defaultPrevented).toBe(true)
    expect(nextFeed).toHaveBeenCalledTimes(1)
    expect(markSpy).toHaveBeenCalledWith(1)
    expect(focusSpy).toHaveBeenLastCalledWith(-1)
  })

  it("if goToNextFeed reports no further feed, focus stays put", (): void => {
    const articles: Article[] = [makeArticle({ url: "a", read_at: null })]
    const focusSpy: Mock = vi.fn()
    const nextFeed: Mock = vi.fn((): boolean => false)
    render(
      <Harness
        filtered={articles}
        initialFocus={0}
        canJumpToNextFeed={true}
        goToNextFeed={nextFeed}
        onFocus={focusSpy}
      />,
    )

    press(" ")

    expect(nextFeed).toHaveBeenCalledTimes(1)
    expect(focusSpy).toHaveBeenLastCalledWith(0)
  })

  it("toggling the hint flag flips Space between inert and active", (): void => {
    const articles: Article[] = [makeArticle({ url: "a", read_at: null })]
    const focusSpy: Mock = vi.fn()
    const nextFeed: Mock = vi.fn((): boolean => true)
    const { rerender } = render(
      <Harness
        filtered={articles}
        initialFocus={0}
        canJumpToNextFeed={false}
        goToNextFeed={nextFeed}
        onFocus={focusSpy}
      />,
    )

    let event: KeyboardEvent = press(" ")
    expect(event.defaultPrevented).toBe(false)
    expect(nextFeed).not.toHaveBeenCalled()

    rerender(
      <Harness
        filtered={articles}
        initialFocus={0}
        canJumpToNextFeed={true}
        goToNextFeed={nextFeed}
        onFocus={focusSpy}
      />,
    )
    event = press(" ")
    expect(event.defaultPrevented).toBe(true)
    expect(nextFeed).toHaveBeenCalledTimes(1)
  })

  it("from the tail feed, jumps to the first unread feed at the head (wrap-around)", (): void => {
    // End-to-end wiring check: real `nextUnreadFeedId` over orderedFeedIds=
    // [1,2,3] with current=3 and unread only at feed 1 must resolve to 1 via
    // the wrap-around path, and Space (with the hint visible) must invoke
    // `goToNextFeed` so the route would navigate to feed 1.
    const orderedFeedIds: number[] = [1, 2, 3]
    const unread: Map<number, number> = new Map([[1, 5]])
    const resolvedTargets: number[] = []
    const goToNextFeed = (): boolean => {
      const target: number | null = nextUnreadFeedId(orderedFeedIds, 3, unread)
      if (target == null) return false
      resolvedTargets.push(target)
      return true
    }
    const articles: Article[] = [makeArticle({ url: "tail", read_at: null })]
    const focusSpy: Mock = vi.fn()

    render(
      <Harness
        filtered={articles}
        initialFocus={0}
        canJumpToNextFeed={true}
        goToNextFeed={goToNextFeed}
        onFocus={focusSpy}
      />,
    )

    const event: KeyboardEvent = press(" ")

    expect(event.defaultPrevented).toBe(true)
    expect(resolvedTargets).toEqual([1])
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
      key: "u",
      bubbles: true,
      cancelable: true,
      metaKey: true,
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
      key: "u",
      bubbles: true,
      cancelable: true,
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
