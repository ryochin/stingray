import { act, render } from "@testing-library/react"
import type { JSX } from "react"
import { useRef, useState } from "react"
import { describe, expect, it } from "vitest"
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

interface HarnessHandle {
  fireJAtEnd: () => void
  getHint: () => "jump" | "end" | null
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
