import { act, cleanup, renderHook } from "@testing-library/react"
import type { RefObject } from "react"
import type { Mock } from "vitest"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { useElementHeight } from "./useElementHeight"

// Captures the callback so tests trigger resizes by hand; happy-dom has no
// layout to fire real ones.
class FakeResizeObserver {
  static instances: FakeResizeObserver[] = []
  observe: Mock = vi.fn()
  unobserve: Mock = vi.fn()
  disconnect: Mock = vi.fn()

  constructor(readonly callback: ResizeObserverCallback) {
    FakeResizeObserver.instances.push(this)
  }

  fire(): void {
    this.callback([], this as unknown as ResizeObserver)
  }
}

let height: number

function measuredElement(): HTMLDivElement {
  const el: HTMLDivElement = document.createElement("div")
  vi.spyOn(el, "getBoundingClientRect").mockImplementation(
    (): DOMRect => ({ height }) as DOMRect,
  )
  return el
}

function ref<T>(current: T): RefObject<T> {
  return { current }
}

beforeEach((): void => {
  height = 48
  FakeResizeObserver.instances = []
  vi.stubGlobal("ResizeObserver", FakeResizeObserver)
})

afterEach((): void => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe("useElementHeight", (): void => {
  it("measures on mount without waiting for a resize", (): void => {
    const el: HTMLDivElement = measuredElement()
    const { result } = renderHook(() => useElementHeight(ref(el)))

    expect(result.current).toBe(48)
    expect(FakeResizeObserver.instances[0].observe).toHaveBeenCalledWith(el)
  })

  it("re-measures on each observed resize", (): void => {
    const { result } = renderHook(() =>
      useElementHeight(ref(measuredElement())),
    )

    height = 120
    act((): void => FakeResizeObserver.instances[0].fire())
    expect(result.current).toBe(120)
  })

  it("stays at 0 without an element", (): void => {
    const { result } = renderHook(() => useElementHeight(ref(null)))
    expect(result.current).toBe(0)
    expect(FakeResizeObserver.instances).toHaveLength(0)
  })

  it("disconnects on unmount", (): void => {
    const { unmount } = renderHook(() =>
      useElementHeight(ref(measuredElement())),
    )
    unmount()
    expect(FakeResizeObserver.instances[0].disconnect).toHaveBeenCalled()
  })
})
