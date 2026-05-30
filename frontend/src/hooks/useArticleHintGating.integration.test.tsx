import { act, render } from "@testing-library/react"
import type { JSX } from "react"
import { useRef, useState } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { Article, Selection } from "../api/client"
import { useArticleKeyboard } from "./useArticleKeyboard"
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

// Controllable IntersectionObserver replacement. Tests drive intersection
// transitions via `triggerIntersection(el, true|false)`; the production
// code is otherwise untouched.
interface RegisteredObserver {
  callback: IntersectionObserverCallback
  observed: Set<Element>
  instance: IntersectionObserver
}
const registeredObservers: RegisteredObserver[] = []

class MockIntersectionObserver implements IntersectionObserver {
  root: Element | Document | null
  rootMargin: string
  thresholds: ReadonlyArray<number>
  private record: RegisteredObserver
  constructor(
    callback: IntersectionObserverCallback,
    options?: IntersectionObserverInit,
  ) {
    this.root = (options?.root as Element | Document | null) ?? null
    this.rootMargin = options?.rootMargin ?? "0px"
    const t: number | number[] = options?.threshold ?? 0
    this.thresholds = Array.isArray(t) ? t : [t]
    this.record = {
      callback,
      observed: new Set<Element>(),
      instance: this,
    }
    registeredObservers.push(this.record)
  }
  observe(target: Element): void {
    this.record.observed.add(target)
  }
  unobserve(target: Element): void {
    this.record.observed.delete(target)
  }
  disconnect(): void {
    this.record.observed.clear()
    const i: number = registeredObservers.indexOf(this.record)
    if (i >= 0) registeredObservers.splice(i, 1)
  }
  takeRecords(): IntersectionObserverEntry[] {
    return []
  }
}

function triggerIntersection(target: Element, isIntersecting: boolean): void {
  for (const rec of registeredObservers) {
    if (!rec.observed.has(target)) continue
    const entry: IntersectionObserverEntry = {
      target,
      isIntersecting,
      intersectionRatio: isIntersecting ? 1 : 0,
      boundingClientRect: target.getBoundingClientRect(),
      intersectionRect: target.getBoundingClientRect(),
      rootBounds: null,
      time: 0,
    }
    act((): void => {
      rec.callback([entry], rec.instance)
    })
  }
}

interface HarnessHandle {
  sentinelRef: (el: HTMLDivElement | null) => void
  fireJAtEnd: () => void
}

interface HarnessProps {
  articles: Article[]
  initialFocus: number
  nextUnreadFeed: number | null
  goToNextFeed: () => boolean
  onReady: (handle: HarnessHandle) => void
  showUnreadOnly?: boolean
}

function Harness({
  articles,
  initialFocus,
  nextUnreadFeed,
  goToNextFeed,
  onReady,
  showUnreadOnly = false,
}: HarnessProps): JSX.Element {
  const mainRef = useRef<HTMLDivElement | null>(null)
  const stickyHeaderRef = useRef<HTMLDivElement | null>(null)
  const [focusIndex, setFocusIndex] = useState<number>(initialFocus)
  const selection: Selection = { type: "all" }

  const controller = useArticleListController({
    filtered: articles,
    focusIndex,
    setFocusIndex,
    mainRef,
    stickyHeaderRef,
    headerHeight: 0,
    selection,
    showUnreadOnly,
    timeRangeId: "all",
    scheduleRead: (): void => {},
    hasSessionRead: (): boolean => false,
    nextUnreadFeed,
  })

  useArticleKeyboard({
    filtered: articles,
    setFocusIndex,
    markFocusedAsRead: controller.markFocusedAsRead,
    canJumpToNextFeed:
      controller.caughtUpHint === "jump" && controller.caughtUpVisible,
    scheduleRead: (): void => {},
    toggleRead: (): void => {},
    markAllRead: (): void => {},
    goToNextFeed,
    onJAtEnd: controller.onJAtEnd,
    onKBeforeMove: controller.onKBeforeMove,
    toggleUnreadFilter: (): void => {},
    setShowHelp: (): void => {},
  })

  onReady({
    sentinelRef: controller.caughtUpSentinelRef,
    fireJAtEnd: controller.onJAtEnd,
  })

  return (
    <div>
      <div ref={stickyHeaderRef} />
      <div ref={mainRef} style={{ height: 200, overflow: "auto" }} />
    </div>
  )
}

function pressSpace(): KeyboardEvent {
  const event: KeyboardEvent = new KeyboardEvent("keydown", {
    key: " ",
    bubbles: true,
    cancelable: true,
  })
  act((): void => {
    document.body.dispatchEvent(event)
  })
  return event
}

beforeEach((): void => {
  ;(globalThis as { IntersectionObserver: unknown }).IntersectionObserver =
    MockIntersectionObserver
})

afterEach((): void => {
  registeredObservers.length = 0
  document.body.innerHTML = ""
})

describe("Space-key gating wires through controller + keyboard", (): void => {
  it("stays inert before the second j-at-end exposes the 'jump' hint", (): void => {
    let handle: HarnessHandle | undefined
    const goToNextFeed = vi.fn((): boolean => true)
    render(
      <Harness
        articles={[makeArticle({ url: "a", read_at: null })]}
        initialFocus={0}
        nextUnreadFeed={42}
        goToNextFeed={goToNextFeed}
        onReady={(h: HarnessHandle): void => {
          handle = h
        }}
      />,
    )
    expect(handle).toBeDefined()

    // Attach the sentinel to a real DOM node and report it as on-screen.
    // Even with visibility=true, Space must remain inert until the hint
    // state actually flips to "jump".
    const sentinel: HTMLDivElement = document.createElement("div")
    document.body.appendChild(sentinel)
    act((): void => {
      handle?.sentinelRef(sentinel)
    })
    triggerIntersection(sentinel, true)

    let event: KeyboardEvent = pressSpace()
    expect(event.defaultPrevented).toBe(false)
    expect(goToNextFeed).not.toHaveBeenCalled()

    // First j-at-end only pulses; still no hint, Space remains inert.
    act((): void => {
      handle?.fireJAtEnd()
    })
    event = pressSpace()
    expect(event.defaultPrevented).toBe(false)
    expect(goToNextFeed).not.toHaveBeenCalled()

    // Second j-at-end surfaces the "jump" hint; Space now jumps.
    act((): void => {
      handle?.fireJAtEnd()
    })
    event = pressSpace()
    expect(event.defaultPrevented).toBe(true)
    expect(goToNextFeed).toHaveBeenCalledTimes(1)
  })

  it("with nextUnreadFeed=null, the 'end' hint never enables Space", (): void => {
    let handle: HarnessHandle | undefined
    const goToNextFeed = vi.fn((): boolean => false)
    render(
      <Harness
        articles={[makeArticle({ url: "a", read_at: null })]}
        initialFocus={0}
        nextUnreadFeed={null}
        goToNextFeed={goToNextFeed}
        onReady={(h: HarnessHandle): void => {
          handle = h
        }}
      />,
    )

    const sentinel: HTMLDivElement = document.createElement("div")
    document.body.appendChild(sentinel)
    act((): void => {
      handle?.sentinelRef(sentinel)
    })
    triggerIntersection(sentinel, true)

    act((): void => {
      handle?.fireJAtEnd()
    })
    act((): void => {
      handle?.fireJAtEnd()
    })

    const event: KeyboardEvent = pressSpace()
    expect(event.defaultPrevented).toBe(false)
    expect(goToNextFeed).not.toHaveBeenCalled()
  })

  it("scrolling the sentinel out of view disarms Space even while hint is 'jump'", (): void => {
    // Toggle visibility before the first successful jump so the focus
    // reset that follows `goToNextFeed=true` doesn't muddy the hint state
    // we're trying to observe.
    let handle: HarnessHandle | undefined
    const goToNextFeed = vi.fn((): boolean => true)
    render(
      <Harness
        articles={[makeArticle({ url: "a", read_at: null })]}
        initialFocus={0}
        nextUnreadFeed={42}
        goToNextFeed={goToNextFeed}
        onReady={(h: HarnessHandle): void => {
          handle = h
        }}
      />,
    )

    const sentinel: HTMLDivElement = document.createElement("div")
    document.body.appendChild(sentinel)
    act((): void => {
      handle?.sentinelRef(sentinel)
    })

    // Arm the hint: hint becomes "jump" and sentinel is visible.
    triggerIntersection(sentinel, true)
    act((): void => {
      handle?.fireJAtEnd()
    })
    act((): void => {
      handle?.fireJAtEnd()
    })

    // Sentinel scrolls out of view → Space must be inert despite the
    // hint state being "jump".
    triggerIntersection(sentinel, false)
    let event: KeyboardEvent = pressSpace()
    expect(event.defaultPrevented).toBe(false)
    expect(goToNextFeed).not.toHaveBeenCalled()

    // Sentinel back in view → Space re-engages without requiring another
    // j-at-end pair, because the hint state was never reset.
    triggerIntersection(sentinel, true)
    event = pressSpace()
    expect(event.defaultPrevented).toBe(true)
    expect(goToNextFeed).toHaveBeenCalledTimes(1)
  })
})

describe("Space-key arming on an empty unread feed", (): void => {
  it("jumps without any j-at-end when the empty-state hint is visible", (): void => {
    let handle: HarnessHandle | undefined
    const goToNextFeed = vi.fn((): boolean => true)
    render(
      <Harness
        articles={[]}
        initialFocus={-1}
        nextUnreadFeed={42}
        goToNextFeed={goToNextFeed}
        showUnreadOnly={true}
        onReady={(h: HarnessHandle): void => {
          handle = h
        }}
      />,
    )

    const sentinel: HTMLDivElement = document.createElement("div")
    document.body.appendChild(sentinel)
    act((): void => {
      handle?.sentinelRef(sentinel)
    })
    triggerIntersection(sentinel, true)

    const event: KeyboardEvent = pressSpace()
    expect(event.defaultPrevented).toBe(true)
    expect(goToNextFeed).toHaveBeenCalledTimes(1)
  })

  it("with nextUnreadFeed=null, the empty 'end' hint keeps Space inert", (): void => {
    let handle: HarnessHandle | undefined
    const goToNextFeed = vi.fn((): boolean => false)
    render(
      <Harness
        articles={[]}
        initialFocus={-1}
        nextUnreadFeed={null}
        goToNextFeed={goToNextFeed}
        showUnreadOnly={true}
        onReady={(h: HarnessHandle): void => {
          handle = h
        }}
      />,
    )

    const sentinel: HTMLDivElement = document.createElement("div")
    document.body.appendChild(sentinel)
    act((): void => {
      handle?.sentinelRef(sentinel)
    })
    triggerIntersection(sentinel, true)

    const event: KeyboardEvent = pressSpace()
    expect(event.defaultPrevented).toBe(false)
    expect(goToNextFeed).not.toHaveBeenCalled()
  })

  it("stays inert while the sentinel is out of view", (): void => {
    let handle: HarnessHandle | undefined
    const goToNextFeed = vi.fn((): boolean => true)
    render(
      <Harness
        articles={[]}
        initialFocus={-1}
        nextUnreadFeed={42}
        goToNextFeed={goToNextFeed}
        showUnreadOnly={true}
        onReady={(h: HarnessHandle): void => {
          handle = h
        }}
      />,
    )

    const sentinel: HTMLDivElement = document.createElement("div")
    document.body.appendChild(sentinel)
    act((): void => {
      handle?.sentinelRef(sentinel)
    })
    triggerIntersection(sentinel, false)

    const event: KeyboardEvent = pressSpace()
    expect(event.defaultPrevented).toBe(false)
    expect(goToNextFeed).not.toHaveBeenCalled()
  })

  it("does not arm in the All filter (showUnreadOnly=false) empty list", (): void => {
    let handle: HarnessHandle | undefined
    const goToNextFeed = vi.fn((): boolean => true)
    render(
      <Harness
        articles={[]}
        initialFocus={-1}
        nextUnreadFeed={42}
        goToNextFeed={goToNextFeed}
        showUnreadOnly={false}
        onReady={(h: HarnessHandle): void => {
          handle = h
        }}
      />,
    )

    const sentinel: HTMLDivElement = document.createElement("div")
    document.body.appendChild(sentinel)
    act((): void => {
      handle?.sentinelRef(sentinel)
    })
    triggerIntersection(sentinel, true)

    const event: KeyboardEvent = pressSpace()
    expect(event.defaultPrevented).toBe(false)
    expect(goToNextFeed).not.toHaveBeenCalled()
  })
})
