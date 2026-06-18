import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, within } from "@testing-library/react"
import type { UserEvent } from "@testing-library/user-event"
import userEvent from "@testing-library/user-event"
import type { Mock } from "vitest"
import { beforeEach, describe, expect, it, vi } from "vitest"
import type { Feed, Folder, Selection } from "../api/client"
import Sidebar from "./Sidebar"

function folder(id: number, name: string, position = 0): Folder {
  return { id, name, position }
}

function feed(id: number, overrides: Partial<Feed> = {}): Feed {
  return {
    id,
    name: `F${id}`,
    url: `https://f${id}.example.com/`,
    site_url: null,
    translate: false,
    summarize: true,
    enabled: true,
    folder_id: null,
    position: 0,
    last_fetched_at: null,
    consecutive_failures: 0,
    last_error: null,
    extraction_rules: null,
    created_at: "2024-01-01T00:00:00Z",
    ...overrides,
  }
}

function renderSidebar({
  feeds,
  folders,
  selection = { type: "all" },
  unreadCounts = new Map(),
}: {
  feeds: Feed[]
  folders: Folder[]
  selection?: Selection
  unreadCounts?: Map<number, number>
}) {
  const client: QueryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  })
  client.setQueryData(["feeds"], feeds)
  client.setQueryData(["folders"], folders)
  const onSelect: Mock<(sel: Selection) => void> =
    vi.fn<(sel: Selection) => void>()
  const utils = render(
    <QueryClientProvider client={client}>
      <Sidebar
        selection={selection}
        onSelect={onSelect}
        unreadCounts={unreadCounts}
      />
    </QueryClientProvider>,
  )
  return { ...utils, onSelect }
}

beforeEach((): void => {
  sessionStorage.clear()
})

describe("Sidebar", () => {
  it("renders All Feeds and total unread badge", () => {
    renderSidebar({
      feeds: [feed(1, { folder_id: 10 })],
      folders: [folder(10, "Tech")],
      unreadCounts: new Map([[1, 3]]),
    })
    expect(screen.getByText("All Feeds")).toBeInTheDocument()
    const allFeedsButton = screen.getByText("All Feeds").closest("button")!
    expect(within(allFeedsButton).getByText("3")).toBeInTheDocument()
  })

  it("renders folders and their feeds", () => {
    renderSidebar({
      feeds: [feed(1, { name: "F1", folder_id: 10 })],
      folders: [folder(10, "Tech")],
    })
    expect(screen.getByText("Tech")).toBeInTheDocument()
    expect(screen.getByText("F1")).toBeInTheDocument()
  })

  it("renders uncategorized feeds above folders so new feeds surface at top", () => {
    renderSidebar({
      feeds: [
        feed(1, { name: "Folded", folder_id: 10 }),
        feed(2, { name: "Loose", folder_id: null }),
      ],
      folders: [folder(10, "Tech")],
    })
    expect(screen.getByText("Uncategorized")).toBeInTheDocument()
    const loose = screen.getByText("Loose")
    const tech = screen.getByText("Tech")
    // Uncategorized section precedes the folder section in document order.
    expect(loose.compareDocumentPosition(tech)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    )
  })

  it("hides disabled feeds", () => {
    renderSidebar({
      feeds: [
        feed(1, { name: "Shown", enabled: true }),
        feed(2, { name: "Hidden", enabled: false }),
      ],
      folders: [],
    })
    expect(screen.getByText("Shown")).toBeInTheDocument()
    expect(screen.queryByText("Hidden")).toBeNull()
  })

  it("calls onSelect when a feed is clicked", async (): Promise<void> => {
    const user: UserEvent = userEvent.setup()
    const { onSelect } = renderSidebar({
      feeds: [feed(1, { name: "F1" })],
      folders: [],
    })
    await user.click(screen.getByText("F1"))
    expect(onSelect).toHaveBeenCalledWith({ type: "feed", id: 1 })
  })

  it("caret button exposes aria-label and aria-expanded reflecting state", async (): Promise<void> => {
    const user: UserEvent = userEvent.setup()
    renderSidebar({
      feeds: [feed(1, { name: "ChildFeed", folder_id: 10 })],
      folders: [folder(10, "Tech")],
    })
    const caret = screen.getByRole("button", { name: "Collapse Tech" })
    expect(caret).toHaveAttribute("aria-expanded", "true")
    await user.click(caret)
    const collapsedCaret = screen.getByRole("button", { name: "Expand Tech" })
    expect(collapsedCaret).toHaveAttribute("aria-expanded", "false")
  })

  it("collapses a folder on chevron click, hiding child feeds", async (): Promise<void> => {
    const user: UserEvent = userEvent.setup()
    renderSidebar({
      feeds: [feed(1, { name: "ChildFeed", folder_id: 10 })],
      folders: [folder(10, "Tech")],
    })
    expect(screen.getByText("ChildFeed")).toBeInTheDocument()
    // The open-state chevron "▾" becomes "▸" on collapse; find its button by text.
    const chevron = screen.getByText("\u25BE").closest("button")!
    await user.click(chevron)
    expect(screen.queryByText("ChildFeed")).toBeNull()
  })

  it("alt-click on any chevron collapses all folders", async (): Promise<void> => {
    const user: UserEvent = userEvent.setup()
    renderSidebar({
      feeds: [
        feed(1, { name: "FeedA", folder_id: 10 }),
        feed(2, { name: "FeedB", folder_id: 20 }),
        feed(3, { name: "FeedC", folder_id: 30 }),
      ],
      folders: [folder(10, "Tech"), folder(20, "News"), folder(30, "Misc")],
    })
    expect(screen.getByText("FeedA")).toBeInTheDocument()
    expect(screen.getByText("FeedB")).toBeInTheDocument()
    expect(screen.getByText("FeedC")).toBeInTheDocument()
    const firstChevron = screen.getAllByText("\u25BE")[0].closest("button")!
    await user.keyboard("{Alt>}")
    await user.click(firstChevron)
    await user.keyboard("{/Alt}")
    expect(screen.queryByText("FeedA")).toBeNull()
    expect(screen.queryByText("FeedB")).toBeNull()
    expect(screen.queryByText("FeedC")).toBeNull()
    const saved: string | null = sessionStorage.getItem("collapsed-folders")
    expect(saved).not.toBeNull()
    expect(new Set(JSON.parse(saved as string) as number[])).toEqual(
      new Set([10, 20, 30]),
    )
  })

  it("alt-click preserves auto-expansion of the selected feed's folder", async (): Promise<void> => {
    const user: UserEvent = userEvent.setup()
    renderSidebar({
      feeds: [
        feed(1, { name: "FeedA", folder_id: 10 }),
        feed(2, { name: "FeedB", folder_id: 20 }),
      ],
      folders: [folder(10, "Tech"), folder(20, "News")],
      selection: { type: "feed", id: 1 },
    })
    // Alt-click any chevron. Folder 10 should re-expand because it contains
    // the selected feed; folder 20 should stay collapsed.
    const firstChevron = screen.getAllByText("\u25BE")[0].closest("button")!
    await user.keyboard("{Alt>}")
    await user.click(firstChevron)
    await user.keyboard("{/Alt}")
    expect(screen.getByText("FeedA")).toBeInTheDocument()
    expect(screen.queryByText("FeedB")).toBeNull()
    // After useEffect settles, sessionStorage should reflect the on-screen
    // state: only the non-selected folder remains in `collapsed-folders`.
    const saved: string | null = sessionStorage.getItem("collapsed-folders")
    expect(saved).not.toBeNull()
    expect(new Set(JSON.parse(saved as string) as number[])).toEqual(
      new Set([20]),
    )
  })

  it("folder badge shows sum of unread in its feeds", () => {
    renderSidebar({
      feeds: [feed(1, { folder_id: 10 }), feed(2, { folder_id: 10 })],
      folders: [folder(10, "Tech")],
      unreadCounts: new Map([
        [1, 4],
        [2, 3],
      ]),
    })
    const folderButton = screen.getByText("Tech").closest("button")!
    expect(within(folderButton).getByText("7")).toBeInTheDocument()
  })

  it("shows failure marker on feeds with >= 3 consecutive failures", () => {
    renderSidebar({
      feeds: [
        feed(1, { name: "Healthy", consecutive_failures: 2 }),
        feed(2, { name: "Broken", consecutive_failures: 5 }),
      ],
      folders: [],
    })
    // "!!!" is our warning indicator (intentional for quick visual scan).
    expect(screen.getAllByText("!!!")).toHaveLength(1)
  })

  it("auto-expands the folder containing the selected feed", () => {
    // Seed sessionStorage to pre-collapse folder 10.
    sessionStorage.setItem("collapsed-folders", JSON.stringify([10]))
    renderSidebar({
      feeds: [feed(1, { name: "ChildFeed", folder_id: 10 })],
      folders: [folder(10, "Tech")],
      selection: { type: "feed", id: 1 },
    })
    // Because the selected feed lives in folder 10, the auto-expand effect should
    // remove it from `collapsed`, so ChildFeed is visible.
    expect(screen.getByText("ChildFeed")).toBeInTheDocument()
  })
})
