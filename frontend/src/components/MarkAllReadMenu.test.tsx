/**
 * MarkAllReadMenu: the dropdown behind "Mark all as read". Each option hands
 * its age cutoff to `onChoose`; outside clicks and Escape dismiss the menu,
 * except that an open modal keeps Escape for itself.
 */

import { fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import MarkAllReadMenu from "./MarkAllReadMenu"

const TOGGLE: string = "Mark all as read ▾"

afterEach((): void => {
  // The modal stand-in is appended by hand, outside RTL's cleanup.
  document.body.innerHTML = ""
})

function renderMenu(disabled?: boolean) {
  const onChoose = vi.fn()
  const onChooseUnread = vi.fn()
  render(
    <MarkAllReadMenu
      disabled={disabled}
      onChoose={onChoose}
      onChooseUnread={onChooseUnread}
    />,
  )
  return { onChoose, onChooseUnread }
}

function openMenu(): void {
  fireEvent.click(screen.getByRole("button", { name: TOGGLE }))
}

function isOpen(): boolean {
  return screen.queryByRole("button", { name: "Mark all as unread" }) != null
}

describe("MarkAllReadMenu", (): void => {
  it("cannot be opened while disabled", (): void => {
    renderMenu(true)
    expect(screen.getByRole("button", { name: TOGGLE })).toBeDisabled()
    openMenu()
    expect(isOpen()).toBe(false)
  })

  it("toggles open and closed from its button", (): void => {
    renderMenu()
    openMenu()
    expect(isOpen()).toBe(true)
    openMenu()
    expect(isOpen()).toBe(false)
  })

  it.each([
    ["All", null],
    ["Older than 12 hours", 12],
    ["Older than 48 hours", 48],
    ["Older than 1 week", 168],
    ["Older than 1 month", 720],
  ])("%s passes its cutoff and closes", (label: string, hours:
    | number
    | null): void => {
    const { onChoose, onChooseUnread } = renderMenu()
    openMenu()

    fireEvent.click(screen.getByRole("button", { name: label }))

    expect(onChoose).toHaveBeenCalledExactlyOnceWith(hours)
    expect(onChooseUnread).not.toHaveBeenCalled()
    expect(isOpen()).toBe(false)
  })

  it("Mark all as unread calls its own handler and closes", (): void => {
    const { onChoose, onChooseUnread } = renderMenu()
    openMenu()

    fireEvent.click(screen.getByRole("button", { name: "Mark all as unread" }))

    expect(onChooseUnread).toHaveBeenCalledTimes(1)
    expect(onChoose).not.toHaveBeenCalled()
    expect(isOpen()).toBe(false)
  })

  it("closes on a mousedown outside the menu", (): void => {
    renderMenu()
    openMenu()
    fireEvent.mouseDown(document.body)
    expect(isOpen()).toBe(false)
  })

  it("stays open on a mousedown inside the menu", (): void => {
    renderMenu()
    openMenu()
    // mousedown alone: a full click on an option would close it by design.
    fireEvent.mouseDown(screen.getByRole("button", { name: "All" }))
    expect(isOpen()).toBe(true)
  })

  it("closes on Escape", (): void => {
    renderMenu()
    openMenu()
    fireEvent.keyDown(window, { key: "Escape" })
    expect(isOpen()).toBe(false)
  })

  it("leaves Escape to an open modal", (): void => {
    renderMenu()
    openMenu()
    const modal: HTMLDivElement = document.createElement("div")
    modal.setAttribute("aria-modal", "true")
    document.body.appendChild(modal)

    fireEvent.keyDown(window, { key: "Escape" })
    expect(isOpen()).toBe(true)

    modal.remove()
    fireEvent.keyDown(window, { key: "Escape" })
    expect(isOpen()).toBe(false)
  })

  it("ignores other keys", (): void => {
    renderMenu()
    openMenu()
    fireEvent.keyDown(window, { key: "a" })
    expect(isOpen()).toBe(true)
  })
})
