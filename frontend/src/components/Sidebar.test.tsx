import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import Sidebar from "./Sidebar"
import type { Feed, Folder, Selection } from "../api/client"


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
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  })
  client.setQueryData(["feeds"], feeds)
  client.setQueryData(["folders"], folders)
  const onSelect = vi.fn<(sel: Selection) => void>()
  const utils = render(
    <QueryClientProvider client={client}>
      <Sidebar selection={selection} onSelect={onSelect} unreadCounts={unreadCounts} />
    </QueryClientProvider>,
  )
  return { ...utils, onSelect }
}


beforeEach(() => {
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

  it("renders uncategorized feeds only after folders have content", () => {
    renderSidebar({
      feeds: [
        feed(1, { name: "Folded", folder_id: 10 }),
        feed(2, { name: "Loose", folder_id: null }),
      ],
      folders: [folder(10, "Tech")],
    })
    expect(screen.getByText("Uncategorized")).toBeInTheDocument()
    expect(screen.getByText("Loose")).toBeInTheDocument()
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

  it("calls onSelect when a feed is clicked", async () => {
    const user = userEvent.setup()
    const { onSelect } = renderSidebar({
      feeds: [feed(1, { name: "F1" })],
      folders: [],
    })
    await user.click(screen.getByText("F1"))
    expect(onSelect).toHaveBeenCalledWith({ type: "feed", id: 1 })
  })

  it("collapses a folder on chevron click, hiding child feeds", async () => {
    const user = userEvent.setup()
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

  it("folder badge shows sum of unread in its feeds", () => {
    renderSidebar({
      feeds: [
        feed(1, { folder_id: 10 }),
        feed(2, { folder_id: 10 }),
      ],
      folders: [folder(10, "Tech")],
      unreadCounts: new Map([[1, 4], [2, 3]]),
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
