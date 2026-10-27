/**
 * Guards the virtualized list view: row placement from the virtualizer,
 * the caught-up sentinel slot, and per-card wiring (focus, feed, pending
 * placeholder, click and ref callbacks). The virtualizer is a stand-in
 * exposing only what the view reads.
 */

import type { VirtualItem, Virtualizer } from "@tanstack/react-virtual"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import type { ComponentProps } from "react"
import type { Mock } from "vitest"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { Article, Feed } from "../api/client"
import { makeArticle, makeFeed } from "../test-fixtures"
import { SHORT_SNIPPET_CHARS } from "../utils/articleProcessing"
import ArticleList from "./ArticleList"

const SCROLL_MARGIN: number = 40

function virtualItem(index: number, start: number): VirtualItem {
  return { index, key: index, start, end: start + 100, size: 100, lane: 0 }
}

interface FakeVirtualizer {
  measureElement: Mock
  virtualizer: Virtualizer<HTMLElement, Element>
}

function fakeVirtualizer(items: VirtualItem[]): FakeVirtualizer {
  const measureElement: Mock = vi.fn()
  const virtualizer = {
    getTotalSize: (): number => 1234,
    getVirtualItems: (): VirtualItem[] => items,
    measureElement,
    options: { scrollMargin: SCROLL_MARGIN },
  } as unknown as Virtualizer<HTMLElement, Element>
  return { measureElement, virtualizer }
}

type Props = ComponentProps<typeof ArticleList>

let setRef: Mock
let animate: Mock
let onCardClick: Mock
let onTitleClick: Mock

function renderList(
  articles: Article[],
  overrides: Partial<Props> = {},
  items: VirtualItem[] = articles.map(
    (_: Article, i: number): VirtualItem => virtualItem(i, 200 + i * 100),
  ),
): FakeVirtualizer & { container: HTMLElement; unmount: () => void } {
  const fake: FakeVirtualizer = fakeVirtualizer(items)
  const { container, unmount } = render(
    <ArticleList
      articles={articles}
      focusIndex={-1}
      feedMap={new Map()}
      virtualizer={fake.virtualizer}
      allCaughtUpIndex={-1}
      setRef={setRef}
      onCardClick={onCardClick}
      onTitleClick={onTitleClick}
      caughtUpPulseKey={0}
      {...overrides}
    />,
  )
  return { ...fake, container, unmount }
}

function row(container: HTMLElement, index: number): HTMLElement {
  return container.querySelector(`[data-index="${index}"]`) as HTMLElement
}

function card(container: HTMLElement, index: number): HTMLElement {
  return row(container, index).querySelector('[role="button"]') as HTMLElement
}

beforeEach((): void => {
  setRef = vi.fn()
  onCardClick = vi.fn()
  onTitleClick = vi.fn()
  // CaughtUpIndicator pulses via WAAPI, which happy-dom lacks.
  animate = vi.fn((): { cancel: Mock } => ({ cancel: vi.fn() }))
  Object.defineProperty(Element.prototype, "animate", {
    value: animate,
    configurable: true,
    writable: true,
  })
})

afterEach((): void => {
  cleanup()
  delete (Element.prototype as { animate?: unknown }).animate
})

describe("ArticleList layout", (): void => {
  it("sizes the container from the virtualizer's total size", (): void => {
    const { container } = renderList([])
    expect((container.firstElementChild as HTMLElement).style.height).toBe(
      "1234px",
    )
  })

  it("places rows at start minus scrollMargin and hands them to measureElement", (): void => {
    const { container, measureElement } = renderList([
      makeArticle("https://a.test/0"),
      makeArticle("https://a.test/1"),
    ])

    expect(row(container, 0).style.transform).toBe("translateY(160px)")
    expect(row(container, 1).style.transform).toBe("translateY(260px)")
    expect(measureElement).toHaveBeenCalledWith(row(container, 0))
    expect(measureElement).toHaveBeenCalledWith(row(container, 1))
  })

  it("renders nothing for a virtual item without an article", (): void => {
    const { container } = renderList([makeArticle("https://a.test/0")], {}, [
      virtualItem(0, 200),
      virtualItem(5, 700),
    ])
    expect(row(container, 0)).toBeTruthy()
    expect(row(container, 5)).toBeNull()
  })
})

describe("ArticleList caught-up slot", (): void => {
  const articles: Article[] = [makeArticle("https://a.test/0")]
  const items: VirtualItem[] = [virtualItem(0, 200), virtualItem(1, 300)]

  it("renders the sentinel instead of a card at allCaughtUpIndex", (): void => {
    const caughtUpRef: Mock = vi.fn()
    const { container, measureElement } = renderList(
      articles,
      { allCaughtUpIndex: 1, caughtUpSubLabel: "Press <Space>", caughtUpRef },
      items,
    )

    const sentinel: HTMLElement = row(container, 1)
    expect(sentinel.textContent).toContain("All caught up")
    expect(sentinel.querySelector("kbd")?.textContent).toBe("Space")
    expect(sentinel.querySelector('[role="button"]')).toBeNull()
    expect(sentinel.style.transform).toBe("translateY(260px)")
    expect(measureElement).toHaveBeenCalledWith(sentinel)
    expect(caughtUpRef).toHaveBeenCalledWith(sentinel.firstElementChild)
  })

  it.each([
    [0, "text-text-muted"],
    [3, "text-text"],
  ])("colors the label for pulseKey %i as %s", (pulseKey: number, color: string): void => {
    const { container } = renderList(
      articles,
      { allCaughtUpIndex: 1, caughtUpPulseKey: pulseKey },
      items,
    )
    const inner: Element = row(container, 1).firstElementChild
      ?.firstElementChild as Element
    expect(inner.className.split(" ")).toContain(color)
  })

  it("passes the pulse key through to the sentinel", (): void => {
    renderList(articles, { allCaughtUpIndex: 1, caughtUpPulseKey: 0 }, items)
    expect(animate).not.toHaveBeenCalled()

    cleanup()
    renderList(articles, { allCaughtUpIndex: 1, caughtUpPulseKey: 2 }, items)
    expect(animate).toHaveBeenCalledTimes(1)
  })
})

describe("ArticleList cards", (): void => {
  it("marks only the card at focusIndex as focused", (): void => {
    const { container } = renderList(
      [makeArticle("https://a.test/0"), makeArticle("https://a.test/1")],
      { focusIndex: 1 },
    )
    expect(card(container, 0).className).not.toContain("border-accent-bg")
    expect(card(container, 1).className).toContain("border-accent-bg")
  })

  it("shows the feed name only when the article's feed is known", (): void => {
    const feed: Feed = makeFeed({ id: 1, name: "Known Feed" })
    renderList(
      [
        makeArticle("https://a.test/0", { feed_id: 1 }),
        makeArticle("https://a.test/1", { feed_id: 2 }),
        makeArticle("https://a.test/2", { feed_id: null }),
      ],
      { feedMap: new Map([[1, feed]]) },
    )
    expect(screen.getAllByText("Known Feed")).toHaveLength(1)
  })

  it.each([
    [
      "translation",
      { translate: true },
      "x".repeat(SHORT_SNIPPET_CHARS - 1),
      "Awaiting translation...",
    ],
    [
      "summary",
      { summarize: true },
      "x".repeat(SHORT_SNIPPET_CHARS),
      "Awaiting summary...",
    ],
  ])("derives the %s placeholder from the feed settings", (_: string, feedFlags: Partial<Feed>, snippet: string, text: string): void => {
    renderList(
      [makeArticle("https://a.test/0", { content_snippet: snippet })],
      {
        feedMap: new Map([[1, makeFeed({ id: 1, ...feedFlags })]]),
      },
    )
    expect(screen.getByText(text)).toBeTruthy()
  })

  it("shows no placeholder when the feed does no LLM work", (): void => {
    renderList(
      [
        makeArticle("https://a.test/0", {
          content_snippet: "x".repeat(SHORT_SNIPPET_CHARS),
        }),
      ],
      { feedMap: new Map([[1, makeFeed({ id: 1 })]]) },
    )
    expect(screen.queryByText(/Awaiting/)).toBeNull()
  })

  it("wires card clicks, title clicks and card refs by index", (): void => {
    const { container } = renderList([
      makeArticle("https://a.test/0"),
      makeArticle("https://a.test/1"),
    ])

    fireEvent.click(card(container, 1))
    expect(onCardClick).toHaveBeenCalledWith(1)

    fireEvent.click(screen.getByText("https://a.test/1"))
    expect(onTitleClick).toHaveBeenCalledWith("https://a.test/1")

    expect(setRef).toHaveBeenCalledWith(0, card(container, 0))
    expect(setRef).toHaveBeenCalledWith(1, card(container, 1))
  })

  it("clears the card refs on unmount", (): void => {
    const { unmount } = renderList([
      makeArticle("https://a.test/0"),
      makeArticle("https://a.test/1"),
    ])
    setRef.mockClear()

    unmount()

    expect(setRef).toHaveBeenCalledWith(0, null)
    expect(setRef).toHaveBeenCalledWith(1, null)
  })
})
