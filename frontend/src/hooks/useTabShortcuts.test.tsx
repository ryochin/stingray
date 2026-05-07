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

  it("cleans up the listener on unmount", (): void => {
    const { unmount } = renderWithRouter()
    unmount()
    press("a")
    expect(navigate).not.toHaveBeenCalled()
  })
})
