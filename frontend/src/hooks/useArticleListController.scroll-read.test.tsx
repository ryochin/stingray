import { act, render } from "@testing-library/react"
import type { JSX } from "react"
import { useRef, useState } from "react"
import type { Mock } from "vitest"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { Article, Selection } from "../api/client"
import { useArticleListController } from "./useArticleListController"

// happy-dom has no layout, so the real virtualizer renders no items. This
// stand-in reports every index as rendered and spaces cards 200px apart.
vi.mock("@tanstack/react-virtual", async () => {
  const { useState: useStateInMock } = await import("react")
  return {
    useVirtualizer: (opts: { count: number }): unknown => {
      const [fake] = useStateInMock(() => ({
        count: 0,
        isScrolling: false,
        scrollOffset: 0,
        getVirtualItems(this: { count: number }): { index: number }[] {
          return Array.from({ length: this.count }, (_, index: number) => ({
            index,
          }))
        },
        getOffsetForIndex: (index: number): [number, string] => [
          index * 200,
          "start",
        ],
        scrollToIndex: vi.fn(),
      }))
      fake.count = opts.count
      return fake
    },
  }
})

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

// rAF callbacks are queued and run only when a test flushes them, in order,
// so a smooth focus-scroll frame and a scroll-detector frame interleave the
// way they do in a browser.
let rafQueue: Map<number, FrameRequestCallback>
let nextRafId: number

function flushFrames(timestamp: number = 0): void {
  act((): void => {
    const pending: FrameRequestCallback[] = [...rafQueue.values()]
    rafQueue.clear()
    for (const cb of pending) cb(timestamp)
  })
}

const HEADER_BOTTOM: number = 50

interface Handle {
  focusIndex: number
  setFocusIndex: (i: number) => void
}

interface HarnessProps {
  filtered: Article[]
  scheduleRead: (url: string) => void
  hasSessionRead: (url: string) => boolean
  onReady: (handle: Handle) => void
}

function Harness({
  filtered,
  scheduleRead,
  hasSessionRead,
  onReady,
}: HarnessProps): JSX.Element {
  const mainRef = useRef<HTMLDivElement | null>(null)
  const stickyHeaderRef = useRef<HTMLDivElement | null>(null)
  const [focusIndex, setFocusIndex] = useState<number>(0)
  const selection: Selection = useState<Selection>({ type: "all" })[0]

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
    scheduleRead,
    hasSessionRead,
    nextUnreadFeed: null,
  })
  onReady({ focusIndex, setFocusIndex })

  return (
    <div>
      <div ref={stickyHeaderRef} data-testid="header" />
      <div ref={mainRef} data-testid="main">
        {filtered.map((article: Article, index: number) => (
          <div
            key={article.url}
            data-testid={`card-${article.url}`}
            ref={(el: HTMLDivElement | null): void =>
              controller.setRef(index, el)
            }
          />
        ))}
      </div>
    </div>
  )
}

describe("useArticleListController — auto mark-as-read on down-scroll", (): void => {
  let scheduleRead: Mock<(url: string) => void>
  let handle: Handle
  let main: HTMLElement
  let scrollTop: number

  beforeEach((): void => {
    rafQueue = new Map()
    nextRafId = 1
    vi.stubGlobal(
      "requestAnimationFrame",
      (cb: FrameRequestCallback): number => {
        const id: number = nextRafId++
        rafQueue.set(id, cb)
        return id
      },
    )
    vi.stubGlobal("cancelAnimationFrame", (id: number): void => {
      rafQueue.delete(id)
    })
    // smoothScrollTo measures progress from performance.now() at start to the
    // frame timestamp, so pinning the start makes flushFrames(t) mean "t ms in".
    vi.spyOn(performance, "now").mockReturnValue(0)
    scheduleRead = vi.fn()
  })

  afterEach((): void => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  function mount(
    filtered: Article[],
    hasSessionRead: (url: string) => boolean = (): boolean => false,
  ): (next: Article[]) => void {
    const onReady = (h: Handle): void => {
      handle = h
    }
    const { getByTestId, rerender } = render(
      <Harness
        filtered={filtered}
        scheduleRead={scheduleRead}
        hasSessionRead={hasSessionRead}
        onReady={onReady}
      />,
    )
    stubRect(getByTestId("header"), 0, HEADER_BOTTOM)
    main = getByTestId("main")
    // happy-dom clamps scrollTop without layout; keep a plain value instead.
    scrollTop = 0
    Object.defineProperty(main, "scrollTop", {
      configurable: true,
      get: (): number => scrollTop,
      set: (v: number): void => {
        scrollTop = v
      },
    })
    return (next: Article[]): void => {
      rerender(
        <Harness
          filtered={next}
          scheduleRead={scheduleRead}
          hasSessionRead={hasSessionRead}
          onReady={onReady}
        />,
      )
    }
  }

  // Place each card by URL: [top, bottom] relative to the viewport.
  function placeCards(rects: Record<string, [number, number]>): void {
    for (const [url, [top, bottom]] of Object.entries(rects)) {
      const el: Element | null = main.querySelector(
        `[data-testid="card-${url}"]`,
      )
      if (!el) throw new Error(`card ${url} not rendered`)
      stubRect(el, top, bottom)
    }
  }

  function userScrollTo(top: number): void {
    scrollTop = top
    main.dispatchEvent(new Event("scroll"))
    flushFrames()
  }

  it("marks unread cards that passed under the header and moves focus to the first card still in view", (): void => {
    mount(
      [
        makeArticle({ url: "a", read_at: "2026-10-30T00:00:00Z" }),
        makeArticle({ url: "b" }),
        makeArticle({ url: "c" }),
        makeArticle({ url: "d" }),
        makeArticle({ url: "e" }),
      ],
      (url: string): boolean => url === "b",
    )
    placeCards({
      a: [-500, -300],
      b: [-300, -100],
      c: [-100, HEADER_BOTTOM],
      // Not flush with the header, so snapping focus to d would have to scroll.
      d: [HEADER_BOTTOM + 20, 250],
      e: [250, 450],
    })

    userScrollTo(400)

    // a is already read and b was read this session; only c is new.
    expect(scheduleRead.mock.calls).toEqual([["c"]])
    expect(handle.focusIndex).toBe(3)
    // The user drove the scroll, so the new focus is not animated into view.
    expect(rafQueue.size).toBe(0)
    expect(scrollTop).toBe(400)
  })

  it("does nothing on up-scroll even when cards sit above the header", (): void => {
    mount([makeArticle({ url: "a" }), makeArticle({ url: "b" })])
    placeCards({ a: [HEADER_BOTTOM + 10, 250], b: [250, 450] })
    userScrollTo(300)

    placeCards({ a: [-300, -100], b: [-100, 100] })
    userScrollTo(100)

    expect(scheduleRead).not.toHaveBeenCalled()
    expect(handle.focusIndex).toBe(0)
  })

  it("skips the scroll caused by the focus stabilizer, then reads on the next user scroll", (): void => {
    const rerender = mount([
      makeArticle({ url: "a" }),
      makeArticle({ url: "b" }),
    ])
    placeCards({ a: [HEADER_BOTTOM + 10, 250], b: [250, 450] })

    // A refetch prepends an article; the stabilizer keeps "a" focused and
    // shifts scrollTop by one card (fake offsets are 200px apart).
    rerender([
      makeArticle({ url: "new" }),
      makeArticle({ url: "a" }),
      makeArticle({ url: "b" }),
    ])
    expect(handle.focusIndex).toBe(1)
    expect(scrollTop).toBe(200)
    placeCards({
      new: [-200, -10],
      a: [HEADER_BOTTOM + 10, 250],
      b: [250, 450],
    })
    main.dispatchEvent(new Event("scroll"))
    flushFrames()
    expect(scheduleRead).not.toHaveBeenCalled()

    placeCards({ new: [-300, -110], a: [-90, 150], b: [150, 350] })
    userScrollTo(300)
    expect(scheduleRead.mock.calls).toEqual([["new"]])
  })

  it("ignores scroll events while a j/k smooth focus-scroll is in flight", (): void => {
    mount([
      makeArticle({ url: "a" }),
      makeArticle({ url: "b" }),
      makeArticle({ url: "c" }),
    ])
    placeCards({ a: [-300, -100], b: [-100, 40], c: [240, 440] })

    // Moving focus to c starts a 150ms smooth scroll toward it.
    act((): void => handle.setFocusIndex(2))
    expect(rafQueue.size).toBe(1)

    // A scroll event lands mid-animation; the animation's own frame runs
    // first (halfway, so scrollTop moves down) and keeps its slot armed.
    main.dispatchEvent(new Event("scroll"))
    flushFrames(75)

    expect(scrollTop).toBeGreaterThan(0)
    expect(rafQueue.size).toBe(1)
    expect(scheduleRead).not.toHaveBeenCalled()
  })
})
