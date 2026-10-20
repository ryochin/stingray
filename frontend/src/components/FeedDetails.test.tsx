/**
 * FeedDetails: the expanded panel under a feed. Mostly guards SiteUrlEditor —
 * trimmed saves, empty meaning "clear", no-op saves suppressed, Escape
 * reverting, and server updates flowing in without clobbering a draft — plus
 * the status lines and action buttons around it.
 */

import { fireEvent, render, screen } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"
import type { Feed, FeedStats } from "../api/client"
import { makeFeed } from "../test-fixtures"
import FeedDetails from "./FeedDetails"

function renderDetails(feed: Feed, stats?: FeedStats) {
  const handlers = {
    onDelete: vi.fn(),
    onToggleTranslate: vi.fn(),
    onUpdateSiteUrl: vi.fn(),
  }
  const view = render(<FeedDetails feed={feed} stats={stats} {...handlers} />)
  const rerender = (next: Feed): void => {
    view.rerender(<FeedDetails feed={next} stats={stats} {...handlers} />)
  }
  return { ...handlers, rerender }
}

function siteInput(): HTMLInputElement {
  return screen.getByPlaceholderText("https://example.com/") as HTMLInputElement
}

function saveButton(): HTMLElement {
  return screen.getByRole("button", { name: "Save" })
}

function type(value: string): void {
  fireEvent.change(siteInput(), { target: { value } })
}

describe("SiteUrlEditor", (): void => {
  it("saves the typed URL on Save", (): void => {
    const { onUpdateSiteUrl } = renderDetails(makeFeed())
    type("https://site.test/")
    fireEvent.click(saveButton())
    expect(onUpdateSiteUrl).toHaveBeenCalledExactlyOnceWith(
      "https://site.test/",
    )
  })

  it("saves on Enter", (): void => {
    const { onUpdateSiteUrl } = renderDetails(makeFeed())
    type("https://site.test/")
    fireEvent.keyDown(siteInput(), { key: "Enter" })
    expect(onUpdateSiteUrl).toHaveBeenCalledExactlyOnceWith(
      "https://site.test/",
    )
  })

  it("saves the trimmed value", (): void => {
    // happy-dom trims url inputs on its own, so seed the padding through the
    // server value instead of typing it; the editor must still trim.
    const { onUpdateSiteUrl } = renderDetails(
      makeFeed({ site_url: "  https://site.test/  " }),
    )
    fireEvent.click(saveButton())
    expect(onUpdateSiteUrl).toHaveBeenCalledExactlyOnceWith(
      "https://site.test/",
    )
  })

  it("saves null when the field is cleared", (): void => {
    const { onUpdateSiteUrl } = renderDetails(
      makeFeed({ site_url: "https://site.test/" }),
    )
    type("")
    fireEvent.click(saveButton())
    expect(onUpdateSiteUrl).toHaveBeenCalledExactlyOnceWith(null)
  })

  it("disables Save while nothing changed", (): void => {
    renderDetails(makeFeed({ site_url: "https://site.test/" }))
    expect(saveButton()).toBeDisabled()
    type("https://other.test/")
    expect(saveButton()).toBeEnabled()
    type("https://site.test/")
    expect(saveButton()).toBeDisabled()
  })

  it("does not save on Enter while nothing changed", (): void => {
    const { onUpdateSiteUrl } = renderDetails(
      makeFeed({ site_url: "https://site.test/" }),
    )
    fireEvent.keyDown(siteInput(), { key: "Enter" })
    expect(onUpdateSiteUrl).not.toHaveBeenCalled()
  })

  it("treats an empty field as unchanged for a feed without a site URL", (): void => {
    const { onUpdateSiteUrl } = renderDetails(makeFeed({ site_url: null }))
    expect(saveButton()).toBeDisabled()
    fireEvent.keyDown(siteInput(), { key: "Enter" })
    expect(onUpdateSiteUrl).not.toHaveBeenCalled()
  })

  it("reverts the draft on Escape", (): void => {
    const { onUpdateSiteUrl } = renderDetails(
      makeFeed({ site_url: "https://site.test/" }),
    )
    type("https://other.test/")
    fireEvent.keyDown(siteInput(), { key: "Escape" })
    expect(siteInput().value).toBe("https://site.test/")
    expect(saveButton()).toBeDisabled()
    expect(onUpdateSiteUrl).not.toHaveBeenCalled()
  })

  it("picks up a new server value", (): void => {
    const feed: Feed = makeFeed({ site_url: "https://site.test/" })
    const { rerender } = renderDetails(feed)
    rerender({ ...feed, site_url: "https://moved.test/" })
    expect(siteInput().value).toBe("https://moved.test/")
  })

  it("keeps a draft when a refetch brings the same server value", (): void => {
    const feed: Feed = makeFeed({ site_url: "https://site.test/" })
    const { rerender } = renderDetails(feed)
    type("https://draft.test/")
    rerender({ ...feed, name: "Renamed" })
    expect(siteInput().value).toBe("https://draft.test/")
  })
})

describe("FeedDetails", (): void => {
  it("shows the translate state and toggles it", (): void => {
    const { onToggleTranslate, rerender } = renderDetails(
      makeFeed({ translate: false }),
    )
    const toggle: HTMLElement = screen.getByRole("button", {
      name: "Translate: OFF",
    })
    fireEvent.click(toggle)
    expect(onToggleTranslate).toHaveBeenCalledTimes(1)
    rerender(makeFeed({ translate: true }))
    expect(screen.getByRole("button", { name: "Translate: ON" })).toBeVisible()
  })

  it("deletes from its Delete button", (): void => {
    const { onDelete } = renderDetails(makeFeed())
    fireEvent.click(screen.getByRole("button", { name: "Delete" }))
    expect(onDelete).toHaveBeenCalledTimes(1)
  })

  it.each([
    ["degraded", "Stale: cache is old"],
    ["failing", "Error: cache is old"],
  ] as const)("labels a %s feed's last error", (health: Feed["health"], text: string): void => {
    renderDetails(makeFeed({ health, last_error: "cache is old" }))
    expect(screen.getByText(text)).toBeInTheDocument()
  })

  it("hides a stale last error once the feed is healthy", (): void => {
    renderDetails(makeFeed({ health: "ok", last_error: "cache is old" }))
    expect(screen.queryByText(/cache is old/)).toBeNull()
  })

  it("shows article stats when provided", (): void => {
    renderDetails(makeFeed(), {
      article_count: 12,
      unread_count: 3,
      latest_published: "2026-10-01T00:00:00Z",
      oldest_published: null,
    })
    expect(screen.getByText("Articles: 12")).toBeInTheDocument()
    expect(screen.getByText("Unread: 3")).toBeInTheDocument()
    expect(screen.getByText(/^Latest: /)).toBeInTheDocument()
    expect(screen.queryByText(/^Oldest: /)).toBeNull()
  })

  it("shows the failure count only when there are failures", (): void => {
    const { rerender } = renderDetails(makeFeed({ consecutive_failures: 0 }))
    expect(screen.queryByText(/^Failures:/)).toBeNull()
    rerender(makeFeed({ consecutive_failures: 4 }))
    expect(screen.getByText("Failures: 4")).toBeInTheDocument()
  })
})
