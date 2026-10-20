/**
 * useFocusStabilizer keeps the focused article (and its on-screen position)
 * pinned when a background refetch shifts `filtered` underneath it. These
 * tests drive the hook through `rerender` with a fake virtualizer whose
 * offsets are `index * 100`.
 */

import type { Virtualizer } from "@tanstack/react-virtual"
import { renderHook } from "@testing-library/react"
import type { RefObject } from "react"
import { beforeEach, describe, expect, it, type Mock, vi } from "vitest"
import type { Article, Selection } from "../api/client"
import { makeArticle } from "../test-fixtures"
import type { TimeRangeId } from "../utils/articleView"
import { useFocusStabilizer } from "./useFocusStabilizer"

const ROW: number = 100

interface Props {
  filtered: Article[]
  focusIndex: number
  selection?: Selection
  showUnreadOnly?: boolean
  timeRangeId?: TimeRangeId
}

const ALL: Selection = { type: "all" }
const A: Article = makeArticle("https://a.test/")
const B: Article = makeArticle("https://b.test/")
const C: Article = makeArticle("https://c.test/")
const NEW: Article = makeArticle("https://new.test/")
const NEWER: Article = makeArticle("https://newer.test/")

let main: HTMLDivElement
let mainRef: RefObject<HTMLElement | null>
let programmaticScrollRef: RefObject<boolean>
let setFocusIndex: Mock<(i: number) => void>
let getOffsetForIndex: Mock<
  (index: number, align: string) => [number, string] | undefined
>
let virtualizer: Virtualizer<HTMLElement, Element>

beforeEach((): void => {
  main = document.createElement("div")
  main.scrollTop = 500
  mainRef = { current: main }
  programmaticScrollRef = { current: false }
  setFocusIndex = vi.fn()
  getOffsetForIndex = vi.fn((index: number): [number, string] => [
    index * ROW,
    "start",
  ])
  virtualizer = { getOffsetForIndex } as unknown as Virtualizer<
    HTMLElement,
    Element
  >
})

function renderStabilizer(initial: Props) {
  return renderHook(
    ({
      filtered,
      focusIndex,
      selection = ALL,
      showUnreadOnly = true,
      timeRangeId = "3d",
    }: Props): RefObject<boolean> =>
      useFocusStabilizer({
        filtered,
        focusIndex,
        setFocusIndex,
        virtualizer,
        mainRef,
        selection,
        showUnreadOnly,
        timeRangeId,
        programmaticScrollRef,
      }),
    { initialProps: initial },
  )
}

describe("useFocusStabilizer", (): void => {
  it("follows the focused article when a refetch prepends articles", (): void => {
    const { result, rerender } = renderStabilizer({
      filtered: [A, B, C],
      focusIndex: 1,
    })

    rerender({ filtered: [NEW, A, B, C], focusIndex: 1 })

    expect(setFocusIndex).toHaveBeenCalledWith(2)
    // B moved from offset 100 to 200; the viewport shifts by the same amount.
    expect(main.scrollTop).toBe(500 + ROW)
    expect(programmaticScrollRef.current).toBe(true)
    expect(result.current.current).toBe(true)
  })

  it("keeps following across consecutive shifts", (): void => {
    const { rerender } = renderStabilizer({ filtered: [A, B], focusIndex: 1 })

    rerender({ filtered: [NEW, A, B], focusIndex: 1 })
    expect(setFocusIndex).toHaveBeenLastCalledWith(2)
    // The route applies the rebind before the next refetch lands.
    rerender({ filtered: [NEW, A, B], focusIndex: 2 })
    rerender({ filtered: [NEWER, NEW, A, B], focusIndex: 2 })

    expect(setFocusIndex).toHaveBeenLastCalledWith(3)
    expect(main.scrollTop).toBe(500 + 2 * ROW)
  })

  it("scrolls back up when articles above the focus disappear", (): void => {
    const { rerender } = renderStabilizer({
      filtered: [A, B, C],
      focusIndex: 2,
    })

    rerender({ filtered: [B, C], focusIndex: 2 })

    expect(setFocusIndex).toHaveBeenCalledWith(1)
    expect(main.scrollTop).toBe(500 - ROW)
  })

  it("leaves focus alone when the focused article is gone", (): void => {
    const { result, rerender } = renderStabilizer({
      filtered: [A, B, C],
      focusIndex: 1,
    })

    rerender({ filtered: [A, C], focusIndex: 1 })

    expect(setFocusIndex).not.toHaveBeenCalled()
    expect(main.scrollTop).toBe(500)
    expect(programmaticScrollRef.current).toBe(false)
    expect(result.current.current).toBe(false)
  })

  it("does not undo a focus move made in the same render as a refetch", (): void => {
    // j moved focus from A to B while `filtered` re-memoized into a new array.
    // Reverting to A's index would cancel the user's move.
    const { rerender } = renderStabilizer({
      filtered: [A, B, C],
      focusIndex: 0,
    })

    rerender({ filtered: [A, B, C], focusIndex: 1 })

    expect(setFocusIndex).not.toHaveBeenCalled()
    expect(main.scrollTop).toBe(500)
  })

  it("does nothing when the new array keeps the same article in the slot", (): void => {
    const { rerender } = renderStabilizer({
      filtered: [A, B, C],
      focusIndex: 1,
    })

    rerender({ filtered: [A, B, C, NEW], focusIndex: 1 })

    expect(setFocusIndex).not.toHaveBeenCalled()
    expect(main.scrollTop).toBe(500)
  })

  it.each([
    ["selection", { selection: { type: "feed", id: 9 } as Selection }],
    ["the unread filter", { showUnreadOnly: false }],
    ["the time range", { timeRangeId: "7d" as TimeRangeId }],
  ])("does not follow when %s changes in the same render", (_name: string, change: Partial<Props>): void => {
    const { rerender } = renderStabilizer({
      filtered: [A, B, C],
      focusIndex: 1,
    })

    rerender({ filtered: [NEW, A, B, C], focusIndex: 1, ...change })

    expect(setFocusIndex).not.toHaveBeenCalled()
    expect(main.scrollTop).toBe(500)
  })

  it("rebinds without scrolling when the new offset is unknown", (): void => {
    const { result, rerender } = renderStabilizer({
      filtered: [A, B],
      focusIndex: 1,
    })
    getOffsetForIndex.mockReturnValue(undefined)

    rerender({ filtered: [NEW, A, B], focusIndex: 1 })

    expect(setFocusIndex).toHaveBeenCalledWith(2)
    expect(main.scrollTop).toBe(500)
    expect(programmaticScrollRef.current).toBe(false)
    expect(result.current.current).toBe(true)
  })

  it("takes no snapshot while nothing is focused", (): void => {
    const { rerender } = renderStabilizer({ filtered: [A, B], focusIndex: -1 })

    rerender({ filtered: [NEW, A, B], focusIndex: -1 })

    expect(setFocusIndex).not.toHaveBeenCalled()
    expect(main.scrollTop).toBe(500)
  })
})
