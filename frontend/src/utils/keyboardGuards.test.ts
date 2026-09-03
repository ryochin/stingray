import { afterEach, describe, expect, it } from "vitest"
import { isModalOpen, isShortcutSuppressed } from "./keyboardGuards"

/** Build a keydown event and dispatch it on `target` so `e.target` is set. */
function dispatch(
  target: HTMLElement,
  init: Partial<KeyboardEventInit> = {},
): KeyboardEvent {
  const event: KeyboardEvent = new KeyboardEvent("keydown", {
    key: "u",
    bubbles: true,
    cancelable: true,
    ...init,
  })
  target.dispatchEvent(event)
  return event
}

function mount<T extends HTMLElement>(el: T): T {
  document.body.appendChild(el)
  return el
}

afterEach((): void => {
  document.body.innerHTML = ""
})

describe("isShortcutSuppressed", (): void => {
  it("passes a plain keystroke through", (): void => {
    expect(isShortcutSuppressed(dispatch(document.body))).toBe(false)
  })

  it("suppresses modifier chords, which belong to the browser or the OS", (): void => {
    for (const modifier of ["metaKey", "ctrlKey", "altKey"] as const) {
      expect(
        isShortcutSuppressed(dispatch(document.body, { [modifier]: true })),
      ).toBe(true)
    }
  })

  it("does not suppress Shift, which shortcuts use themselves (Shift+A)", (): void => {
    expect(
      isShortcutSuppressed(dispatch(document.body, { shiftKey: true })),
    ).toBe(false)
  })

  it("suppresses text entry in input, textarea and select", (): void => {
    for (const tag of ["input", "textarea", "select"] as const) {
      const el: HTMLElement = mount(document.createElement(tag))
      expect(isShortcutSuppressed(dispatch(el))).toBe(true)
    }
  })

  it("suppresses text entry in a contenteditable", (): void => {
    const el: HTMLDivElement = mount(document.createElement("div"))
    el.contentEditable = "true"
    expect(isShortcutSuppressed(dispatch(el))).toBe(true)
  })

  it("suppresses mid-composition keystrokes, which belong to the IME", (): void => {
    expect(
      isShortcutSuppressed(dispatch(document.body, { isComposing: true })),
    ).toBe(true)
  })

  it("does NOT suppress an already-claimed event", (): void => {
    // ArticleCard preventDefaults its own Enter to take focus, yet still
    // expects the global open-in-a-new-tab binding to fire. Callers that want
    // to defer to an earlier handler check defaultPrevented themselves.
    const el: HTMLDivElement = mount(document.createElement("div"))
    el.addEventListener("keydown", (e: Event): void => e.preventDefault())
    expect(isShortcutSuppressed(dispatch(el))).toBe(false)
  })
})

describe("isModalOpen", (): void => {
  it("is false with no dialog mounted", (): void => {
    expect(isModalOpen()).toBe(false)
  })

  it("is true while an aria-modal element is mounted, and false once removed", (): void => {
    const modal: HTMLDivElement = mount(document.createElement("div"))
    modal.setAttribute("aria-modal", "true")
    expect(isModalOpen()).toBe(true)

    modal.remove()
    expect(isModalOpen()).toBe(false)
  })
})
