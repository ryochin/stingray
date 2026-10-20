import { renderHook } from "@testing-library/react"
import { describe, expect, it } from "vitest"
import type { Article, Feed, Folder, Selection } from "../api/client"
import { faviconUrl } from "../api/client"
import { makeArticle, makeFeed } from "../test-fixtures"
import { useSelectionHeader } from "./useSelectionHeader"

const FEED: Feed = makeFeed({
  id: 7,
  name: "Seven",
  url: "https://seven.test/feed",
})
const FEED_MAP: Map<number, Feed> = new Map([[FEED.id, FEED]])

function folder(id: number, name: string): Folder {
  return { id, name, position: 0 }
}

const FOLDERS: Folder[] = [folder(3, "Tech"), folder(4, "")]

const neverRead = (): boolean => false

type Input = Parameters<typeof useSelectionHeader>[0]

function input(overrides: Partial<Input> = {}): Input {
  return {
    selection: { type: "all" },
    feedMap: FEED_MAP,
    folders: FOLDERS,
    filtered: [],
    localReadCount: 0,
    hasSessionRead: neverRead,
    ...overrides,
  }
}

function headerFor(
  selection: Selection,
  folders: Folder[] | undefined,
): ReturnType<typeof useSelectionHeader>["selectionHeader"] {
  const { result } = renderHook(() =>
    useSelectionHeader(input({ selection, folders })),
  )
  return result.current.selectionHeader
}

describe("useSelectionHeader selectionHeader", (): void => {
  it("is null for the all selection", (): void => {
    expect(headerFor({ type: "all" }, FOLDERS)).toBeNull()
  })

  it("describes a known feed", (): void => {
    expect(headerFor({ type: "feed", id: 7 }, FOLDERS)).toEqual({
      icon: faviconUrl(FEED),
      label: "Seven",
      feedUrl: "https://seven.test/feed",
    })
  })

  it("is null for an unknown feed", (): void => {
    expect(headerFor({ type: "feed", id: 99 }, FOLDERS)).toBeNull()
  })

  it("describes a known folder without icon or feed URL", (): void => {
    expect(headerFor({ type: "folder", id: 3 }, FOLDERS)).toEqual({
      icon: null,
      label: "Tech",
      feedUrl: null,
    })
  })

  it.each([
    ["an unknown folder", 99, FOLDERS],
    ["folders not loaded yet", 3, undefined],
    ["a folder with an empty name", 4, FOLDERS],
  ])("is null for %s", (_: string, id: number, folders:
    | Folder[]
    | undefined): void => {
    expect(headerFor({ type: "folder", id }, folders)).toBeNull()
  })

  it("follows selection, feed map and folder changes across rerenders", (): void => {
    // Each step changes exactly one dependency; the others keep their
    // references so a stale memo cannot pass by accident.
    const feedSelection: Selection = { type: "feed", id: 8 }
    const folderSelection: Selection = { type: "folder", id: 3 }
    const feedMap: Map<number, Feed> = new Map([
      [8, makeFeed({ id: 8, name: "Eight" })],
    ])
    const { result, rerender } = renderHook(
      (props: Pick<Input, "selection" | "feedMap" | "folders">) =>
        useSelectionHeader(input(props)),
      {
        initialProps: {
          // Widen so later rerenders may pass a folder selection.
          selection: feedSelection as Selection,
          feedMap: FEED_MAP,
          folders: undefined as Folder[] | undefined,
        },
      },
    )
    expect(result.current.selectionHeader).toBeNull()

    rerender({ selection: feedSelection, feedMap, folders: undefined })
    expect(result.current.selectionHeader?.label).toBe("Eight")

    rerender({ selection: folderSelection, feedMap, folders: undefined })
    expect(result.current.selectionHeader).toBeNull()

    rerender({ selection: folderSelection, feedMap, folders: FOLDERS })
    expect(result.current.selectionHeader?.label).toBe("Tech")
  })
})

describe("useSelectionHeader selectedUnreadInView", (): void => {
  const articles: Article[] = [
    makeArticle("https://a.test/unread"),
    makeArticle("https://a.test/read", { read_at: "2026-10-20T00:00:00Z" }),
    makeArticle("https://a.test/session-read"),
    makeArticle("https://a.test/unread-2"),
  ]

  it("counts unread articles that were not read this session", (): void => {
    const sessionRead: Set<string> = new Set(["https://a.test/session-read"])
    const { result } = renderHook(() =>
      useSelectionHeader(
        input({
          filtered: articles,
          hasSessionRead: (url: string): boolean => sessionRead.has(url),
        }),
      ),
    )
    expect(result.current.selectedUnreadInView).toBe(2)
  })

  it("recounts when localReadCount changes even if the callback is stable", (): void => {
    const sessionRead: Set<string> = new Set()
    const hasSessionRead = (url: string): boolean => sessionRead.has(url)
    const { result, rerender } = renderHook(
      ({ localReadCount }: { localReadCount: number }) =>
        useSelectionHeader(
          input({ filtered: articles, hasSessionRead, localReadCount }),
        ),
      { initialProps: { localReadCount: 0 } },
    )
    expect(result.current.selectedUnreadInView).toBe(3)

    sessionRead.add("https://a.test/unread")
    rerender({ localReadCount: 1 })
    expect(result.current.selectedUnreadInView).toBe(2)
  })
})
