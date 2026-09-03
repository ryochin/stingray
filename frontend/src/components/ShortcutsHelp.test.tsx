/**
 * Guards the ShortcutsHelp overlay's focus management. The overlay must pull
 * focus to itself while open so its own keydown handler owns Space/Escape —
 * otherwise focus stays on the article scroll container (<main>) and a Space
 * behind the dialog would natively scroll the list or fire the feed-jump
 * shortcut. On close, focus must return to whatever held it before.
 */

import { fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import type { NavItem } from "./navItems"
import { NAV_ITEMS } from "./navItems"
import ShortcutsHelp from "./ShortcutsHelp"

afterEach((): void => {
  document.body.innerHTML = ""
})

describe("ShortcutsHelp nav entries", (): void => {
  it("lists every navigable tab with its shortcut key", (): void => {
    render(<ShortcutsHelp onClose={vi.fn()} />)
    const navigable: readonly (readonly [string, string])[] = NAV_ITEMS.flatMap(
      (item: NavItem): (readonly [string, string])[] =>
        item.shortcut ? [[item.shortcut, item.label]] : [],
    )
    expect(navigable.length).toBeGreaterThan(0)
    for (const [shortcut, label] of navigable) {
      expect(screen.getByText(shortcut)).toBeInTheDocument()
      expect(screen.getByText(`Go to ${label}`)).toBeInTheDocument()
    }
  })
})

describe("ShortcutsHelp focus management", (): void => {
  it("focuses the overlay on open so Space closes it instead of scrolling", (): void => {
    const onClose = vi.fn()
    render(<ShortcutsHelp onClose={onClose} />)
    const overlay: HTMLElement = screen.getByRole("button", {
      name: "Close keyboard shortcuts help",
    })
    expect(document.activeElement).toBe(overlay)

    const event: boolean = fireEvent.keyDown(overlay, { key: " " })
    expect(onClose).toHaveBeenCalledTimes(1)
    // The handler preventDefaults, so the bubbling Space never reaches the
    // window feed-jump handler (it bails on defaultPrevented).
    expect(event).toBe(false)
  })

  it("restores focus to the previously focused element on close", (): void => {
    const trigger: HTMLButtonElement = document.createElement("button")
    document.body.appendChild(trigger)
    trigger.focus()
    expect(document.activeElement).toBe(trigger)

    const { unmount } = render(<ShortcutsHelp onClose={vi.fn()} />)
    expect(document.activeElement).not.toBe(trigger)

    unmount()
    expect(document.activeElement).toBe(trigger)
  })
})
