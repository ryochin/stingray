import { act, render, renderHook } from "@testing-library/react"
import type { JSX } from "react"
import { useRef, useState } from "react"
import type { Mock } from "vitest"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { Article, Selection } from "../api/client"
import { useArticleListController } from "./useArticleListController"

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

// Inert IntersectionObserver stand-in: it never delivers anything, so the
// tests below observe what the controller decides on its own — without any
// observer callback having fired.
class SilentIntersectionObserver implements IntersectionObserver {
  root: Element | Document | null
  rootMargin: string = "0px"
  thresholds: ReadonlyArray<number> = [0]
  constructor(
    _callback: IntersectionObserverCallback,
    options?: IntersectionObserverInit,
  ) {
    this.root = (options?.root as Element | Document | null) ?? null
  }
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
  takeRecords(): IntersectionObserverEntry[] {
    return []
  }
}

// happy-dom reports an all-zero rect for every element, so the vertical
// overlap the controller computes has to be stated explicitly.
function stubRect(el: Element, top: number, bottom: number): void {
  Object.defineProperty(el, "getBoundingClientRect", {
    configurable: true,
    value: (): DOMRect =>
      ({
        top,
        bottom,
        height: bottom - top,
        left: 0,
        right: 0,
        width: 0,
        x: 0,
        y: top,
        toJSON: (): unknown => ({}),
      }) as DOMRect,
  })
}

interface HarnessHandle {
  fireJAtEnd: () => void
  getHint: () => "jump" | "end" | null
  sentinelRef: (el: HTMLDivElement | null) => void
  getVisible: () => boolean
  getMain: () => HTMLDivElement | null
}

interface HarnessProps {
  nextUnreadFeed: number | null
  filtered?: Article[]
  onReady: (handle: HarnessHandle) => void
}

function Harness({
  nextUnreadFeed,
  filtered = [makeArticle({ url: "a" })],
  onReady,
}: HarnessProps): JSX.Element {
  const mainRef = useRef<HTMLDivElement | null>(null)
  const stickyHeaderRef = useRef<HTMLDivElement | null>(null)
  const [focusIndex, setFocusIndex] = useState<number>(0)

  const selection: Selection = { type: "all" }

  const controller = useArticleListController({
    filtered,
    focusIndex,
    setFocusIndex,
    mainRef,
    stickyHeaderRef,
    headerHeight: 0,
    selection,
    showUnreadOnly: false,
    timeRangeId: "all",
    scheduleRead: (): void => {},
    hasSessionRead: (): boolean => false,
    nextUnreadFeed,
  })

  onReady({
    fireJAtEnd: controller.onJAtEnd,
    getHint: (): "jump" | "end" | null => controller.caughtUpHint,
    sentinelRef: controller.caughtUpSentinelRef,
    getVisible: (): boolean => controller.caughtUpVisible,
    getMain: (): HTMLDivElement | null => mainRef.current,
  })

  return (
    <div>
      <div ref={stickyHeaderRef} />
      <div ref={mainRef} style={{ height: 200, overflow: "auto" }} />
    </div>
  )
}

describe("useArticleListController — caughtUpHint branch on consecutive j-at-end", (): void => {
  it("surfaces 'jump' hint on the second j-at-end when a next unread feed exists (wrap-around case)", (): void => {
    let handle: HarnessHandle | undefined
    render(
      <Harness
        nextUnreadFeed={42}
        onReady={(h: HarnessHandle): void => {
          handle = h
        }}
      />,
    )
    expect(handle).toBeDefined()

    // First press only pulses; no hint yet. Always read via the freshly-
    // assigned `handle` so we observe state from the latest render.
    act((): void => {
      handle?.fireJAtEnd()
    })
    expect(handle?.getHint()).toBeNull()

    // Second consecutive press should reflect nextUnreadFeed != null as "jump".
    act((): void => {
      handle?.fireJAtEnd()
    })
    expect(handle?.getHint()).toBe("jump")
  })

  it("surfaces 'end' hint on the second j-at-end when no other feed has unread (nextUnreadFeed === null)", (): void => {
    let handle: HarnessHandle | undefined
    render(
      <Harness
        nextUnreadFeed={null}
        onReady={(h: HarnessHandle): void => {
          handle = h
        }}
      />,
    )

    act((): void => {
      handle?.fireJAtEnd()
    })
    act((): void => {
      handle?.fireJAtEnd()
    })
    expect(handle?.getHint()).toBe("end")
  })
})

describe("useArticleListController — caught-up sentinel visibility", (): void => {
  beforeEach((): void => {
    ;(globalThis as { IntersectionObserver: unknown }).IntersectionObserver =
      SilentIntersectionObserver
  })

  afterEach((): void => {
    document.body.innerHTML = ""
  })

  function mountSentinel(
    handle: HarnessHandle | undefined,
    top: number,
    bottom: number,
  ): HTMLDivElement {
    const sentinel: HTMLDivElement = document.createElement("div")
    // Production renders the sentinel inside the scroll container, so keep
    // the observer's root an actual ancestor of its target.
    ;(handle?.getMain() as HTMLDivElement).appendChild(sentinel)
    stubRect(sentinel, top, bottom)
    act((): void => {
      handle?.sentinelRef(sentinel)
    })
    return sentinel
  }

  it("reports visibility synchronously, before any observer delivery", (): void => {
    // Regression: visibility used to start false and only flip on the
    // observer's first (asynchronous) callback, so a Space press made the
    // instant the hint appeared was dropped.
    let handle: HarnessHandle | undefined
    render(
      <Harness
        nextUnreadFeed={42}
        onReady={(h: HarnessHandle): void => {
          handle = h
        }}
      />,
    )
    const main = handle?.getMain() as HTMLDivElement
    stubRect(main, 0, 200)

    mountSentinel(handle, 150, 190)
    expect(handle?.getVisible()).toBe(true)
  })

  it("reports a sentinel scrolled past the root's bottom edge as hidden", (): void => {
    let handle: HarnessHandle | undefined
    render(
      <Harness
        nextUnreadFeed={42}
        onReady={(h: HarnessHandle): void => {
          handle = h
        }}
      />,
    )
    stubRect(handle?.getMain() as HTMLDivElement, 0, 200)

    mountSentinel(handle, 300, 340)
    expect(handle?.getVisible()).toBe(false)
  })
})

describe("useArticleListController — handleCardClick", (): void => {
  type Updater = (prev: number) => number

  // Virtualization plays no part here, so the hook is called directly and the
  // focus updater it hands to setFocusIndex is applied by hand.
  function clickCard(
    filtered: Article[],
    prev: number,
    index: number,
  ): { next: number; scheduleRead: Mock } {
    const setFocusIndex: Mock = vi.fn()
    const scheduleRead: Mock = vi.fn()
    const { result } = renderHook(() =>
      useArticleListController({
        filtered,
        focusIndex: -1,
        setFocusIndex,
        mainRef: { current: null },
        stickyHeaderRef: { current: null },
        headerHeight: 0,
        selection: { type: "all" },
        showUnreadOnly: false,
        timeRangeId: "all",
        scheduleRead,
        hasSessionRead: (): boolean => false,
        nextUnreadFeed: null,
      }),
    )
    setFocusIndex.mockClear()
    result.current.handleCardClick(index)
    expect(setFocusIndex).toHaveBeenCalledTimes(1)
    const updater: Updater = setFocusIndex.mock.calls[0][0] as Updater
    return { next: updater(prev), scheduleRead }
  }

  const unread: Article[] = [
    makeArticle({ url: "a" }),
    makeArticle({ url: "b" }),
    makeArticle({ url: "c" }),
  ]

  it("moves focus and marks the card being left as read", (): void => {
    const { next, scheduleRead } = clickCard(unread, 0, 2)
    expect(next).toBe(2)
    // The card left behind, not the one clicked.
    expect(scheduleRead).toHaveBeenCalledExactlyOnceWith("a")
  })

  it.each([
    { label: "the same card", filtered: unread, prev: 2 },
    { label: "no focus yet", filtered: unread, prev: -1 },
    {
      label: "an already-read card being left",
      filtered: [
        makeArticle({ url: "a", read_at: "2026-10-26T00:00:00Z" }),
        makeArticle({ url: "b" }),
        makeArticle({ url: "c" }),
      ],
      prev: 0,
    },
  ])("moves focus without marking anything read for $label", ({
    filtered,
    prev,
  }: {
    filtered: Article[]
    prev: number
  }): void => {
    const { next, scheduleRead } = clickCard(filtered, prev, 2)
    expect(next).toBe(2)
    expect(scheduleRead).not.toHaveBeenCalled()
  })
})
