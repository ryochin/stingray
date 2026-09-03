import { act, render } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import type { Mock } from "vitest"
import { afterEach, describe, expect, it, vi } from "vitest"
import { useTabShortcuts } from "./useTabShortcuts"

// Mock react-router's useNavigate so we can assert the navigate calls.
const navigate: Mock = vi.fn()
vi.mock("react-router-dom", async (importActual) => {
  const actual = await importActual<typeof import("react-router-dom")>()
  return { ...actual, useNavigate: (): Mock => navigate }
})

function Harness(): null {
  useTabShortcuts()
  return null
}

function renderWithRouter(): ReturnType<typeof render> {
  return render(
    <MemoryRouter>
      <Harness />
    </MemoryRouter>,
  )
}

function press(
  key: string,
  target: HTMLElement = document.body,
  modifiers: Partial<KeyboardEventInit> = {},
): void {
  act((): void => {
    const event: KeyboardEvent = new KeyboardEvent("keydown", {
      key,
      bubbles: true,
      cancelable: true,
      ...modifiers,
    })
    target.dispatchEvent(event)
  })
}

afterEach((): void => {
  navigate.mockReset()
})

describe("useTabShortcuts", (): void => {
  it("'a' navigates to /", (): void => {
    renderWithRouter()
    press("a")
    expect(navigate).toHaveBeenCalledWith("/")
  })

  it("'f' navigates to /feeds", (): void => {
    renderWithRouter()
    press("f")
    expect(navigate).toHaveBeenCalledWith("/feeds")
  })

  it("'t' navigates to /filters", (): void => {
    renderWithRouter()
    press("t")
    expect(navigate).toHaveBeenCalledWith("/filters")
  })

  it("other keys do nothing", (): void => {
    renderWithRouter()
    press("z")
    expect(navigate).not.toHaveBeenCalled()
  })

  it("modifier keys suppress shortcuts (Cmd+a should not navigate)", (): void => {
    renderWithRouter()
    press("a", document.body, { metaKey: true })
    press("a", document.body, { ctrlKey: true })
    press("a", document.body, { altKey: true })
    expect(navigate).not.toHaveBeenCalled()
  })

  it("typing inside an <input> does not trigger the shortcut", (): void => {
    renderWithRouter()
    const input: HTMLInputElement = document.createElement("input")
    document.body.appendChild(input)
    press("a", input)
    expect(navigate).not.toHaveBeenCalled()
    input.remove()
  })

  it("typing inside a <textarea> does not trigger", (): void => {
    renderWithRouter()
    const ta: HTMLTextAreaElement = document.createElement("textarea")
    document.body.appendChild(ta)
    press("f", ta)
    expect(navigate).not.toHaveBeenCalled()
    ta.remove()
  })

  it("does not navigate while a modal owns the keyboard", (): void => {
    renderWithRouter()
    const modal: HTMLDivElement = document.createElement("div")
    modal.setAttribute("aria-modal", "true")
    document.body.appendChild(modal)
    // Trying a key read off the ShortcutsHelp cheatsheet must not navigate
    // behind the still-open overlay.
    press("t")
    expect(navigate).not.toHaveBeenCalled()
    modal.remove()
  })

  it("ignores keystrokes an IME composition owns", (): void => {
    renderWithRouter()
    press("t", document.body, { isComposing: true })
    expect(navigate).not.toHaveBeenCalled()
  })

  it("ignores an event another handler already claimed", (): void => {
    renderWithRouter()
    const claimer = (e: KeyboardEvent): void => e.preventDefault()
    document.body.addEventListener("keydown", claimer)
    press("t")
    expect(navigate).not.toHaveBeenCalled()
    document.body.removeEventListener("keydown", claimer)
  })

  it("typing inside a contenteditable does not trigger", (): void => {
    renderWithRouter()
    const box: HTMLDivElement = document.createElement("div")
    box.contentEditable = "true"
    document.body.appendChild(box)
    press("t", box)
    expect(navigate).not.toHaveBeenCalled()
    box.remove()
  })

  it("cleans up the listener on unmount", (): void => {
    const { unmount } = renderWithRouter()
    unmount()
    press("a")
    expect(navigate).not.toHaveBeenCalled()
  })
})
