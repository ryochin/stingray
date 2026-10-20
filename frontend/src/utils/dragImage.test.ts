import type { DragEvent } from "react"
import type { Mock } from "vitest"
import { afterEach, describe, expect, it, vi } from "vitest"
import { setCardDragImage } from "./dragImage"

function dragEvent(currentTarget: Element, setDragImage: Mock): DragEvent {
  return {
    currentTarget,
    clientX: 80,
    clientY: 120,
    dataTransfer: { setDragImage },
  } as unknown as DragEvent
}

afterEach((): void => {
  document.body.innerHTML = ""
  vi.restoreAllMocks()
})

describe("setCardDragImage", (): void => {
  it("uses the enclosing card as the drag image, keeping the grab offset", (): void => {
    document.body.innerHTML =
      '<div class="card"><span class="handle"></span></div>'
    const card: Element = document.querySelector(".card") as Element
    const handle: Element = document.querySelector(".handle") as Element
    vi.spyOn(card, "getBoundingClientRect").mockReturnValue({
      left: 30,
      top: 50,
    } as DOMRect)
    const setDragImage: Mock = vi.fn()

    setCardDragImage(dragEvent(handle, setDragImage), ".card")

    expect(setDragImage).toHaveBeenCalledWith(card, 50, 70)
  })

  it("does nothing when no card encloses the target", (): void => {
    document.body.innerHTML = '<span class="handle"></span>'
    const handle: Element = document.querySelector(".handle") as Element
    const setDragImage: Mock = vi.fn()

    setCardDragImage(dragEvent(handle, setDragImage), ".card")

    expect(setDragImage).not.toHaveBeenCalled()
  })
})
