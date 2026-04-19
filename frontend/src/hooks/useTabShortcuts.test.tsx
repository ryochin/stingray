import { describe, it, expect, vi, afterEach } from "vitest"
import { render, act } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { useTabShortcuts } from "./useTabShortcuts"


// Mock react-router's useNavigate so we can assert the navigate calls.
const navigate = vi.fn()
vi.mock("react-router-dom", async (importActual) => {
  const actual = await importActual<typeof import("react-router-dom")>()
  return { ...actual, useNavigate: () => navigate }
})


function Harness() {
  useTabShortcuts()
  return null
}


function renderWithRouter() {
  return render(
    <MemoryRouter>
      <Harness />
    </MemoryRouter>,
  )
}


function press(key: string, target: HTMLElement = document.body, modifiers: Partial<KeyboardEventInit> = {}) {
  act(() => {
    const event = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...modifiers })
    target.dispatchEvent(event)
  })
}


afterEach(() => {
  navigate.mockReset()
})


describe("useTabShortcuts", () => {
  it("'a' navigates to /", () => {
    renderWithRouter()
    press("a")
    expect(navigate).toHaveBeenCalledWith("/")
  })

  it("'f' navigates to /feeds", () => {
    renderWithRouter()
    press("f")
    expect(navigate).toHaveBeenCalledWith("/feeds")
  })

  it("other keys do nothing", () => {
    renderWithRouter()
    press("z")
    expect(navigate).not.toHaveBeenCalled()
  })

  it("modifier keys suppress shortcuts (Cmd+a should not navigate)", () => {
    renderWithRouter()
    press("a", document.body, { metaKey: true })
    press("a", document.body, { ctrlKey: true })
    press("a", document.body, { altKey: true })
    expect(navigate).not.toHaveBeenCalled()
  })

  it("typing inside an <input> does not trigger the shortcut", () => {
    renderWithRouter()
    const input = document.createElement("input")
    document.body.appendChild(input)
    press("a", input)
    expect(navigate).not.toHaveBeenCalled()
    input.remove()
  })

  it("typing inside a <textarea> does not trigger", () => {
    renderWithRouter()
    const ta = document.createElement("textarea")
    document.body.appendChild(ta)
    press("f", ta)
    expect(navigate).not.toHaveBeenCalled()
    ta.remove()
  })

  it("cleans up the listener on unmount", () => {
    const { unmount } = renderWithRouter()
    unmount()
    press("a")
    expect(navigate).not.toHaveBeenCalled()
  })
})
