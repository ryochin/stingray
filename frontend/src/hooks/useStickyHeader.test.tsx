import { act, cleanup, renderHook } from "@testing-library/react"
import type { RefObject } from "react"
import type { Mock } from "vitest"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { useStickyHeader } from "./useStickyHeader"

// Captures the callback and options so tests drive intersection by hand;
// happy-dom has no layout to produce real entries.
class FakeIntersectionObserver {
  static instances: FakeIntersectionObserver[] = []
  observe: Mock = vi.fn()
  unobserve: Mock = vi.fn()
  disconnect: Mock = vi.fn()

  constructor(
    readonly callback: IntersectionObserverCallback,
    readonly options?: IntersectionObserverInit,
  ) {
    FakeIntersectionObserver.instances.push(this)
  }

  emit(isIntersecting: boolean): void {
    this.callback(
      [{ isIntersecting } as IntersectionObserverEntry],
      this as unknown as IntersectionObserver,
    )
  }
}

function ref<T>(current: T): RefObject<T> {
  return { current }
}

beforeEach((): void => {
  FakeIntersectionObserver.instances = []
  vi.stubGlobal("IntersectionObserver", FakeIntersectionObserver)
})

afterEach((): void => {
  cleanup()
  vi.unstubAllGlobals()
})

describe("useStickyHeader", (): void => {
  it("observes the sentinel against the scroll root", (): void => {
    const sentinel: HTMLDivElement = document.createElement("div")
    const root: HTMLDivElement = document.createElement("div")
    const { result } = renderHook(() =>
      useStickyHeader(ref(sentinel), ref(root)),
    )

    expect(result.current).toBe(false)
    const [observer] = FakeIntersectionObserver.instances
    expect(observer.options).toEqual({ root, threshold: 0 })
    expect(observer.observe).toHaveBeenCalledWith(sentinel)
  })

  it("is stuck while the sentinel is out of view", (): void => {
    const { result } = renderHook(() =>
      useStickyHeader(
        ref(document.createElement("div")),
        ref(document.createElement("div")),
      ),
    )
    const [observer] = FakeIntersectionObserver.instances

    act((): void => observer.emit(false))
    expect(result.current).toBe(true)

    act((): void => observer.emit(true))
    expect(result.current).toBe(false)
  })

  it.each([
    ["sentinel", null, document.createElement("div")],
    ["scroll root", document.createElement("div"), null],
  ])("does not observe without a %s", (_: string, sentinel: HTMLElement | null, root: HTMLElement | null): void => {
    renderHook(() => useStickyHeader(ref(sentinel), ref(root)))
    expect(FakeIntersectionObserver.instances).toHaveLength(0)
  })

  it("disconnects on unmount", (): void => {
    const { unmount } = renderHook(() =>
      useStickyHeader(
        ref(document.createElement("div")),
        ref(document.createElement("div")),
      ),
    )
    unmount()
    expect(FakeIntersectionObserver.instances[0].disconnect).toHaveBeenCalled()
  })
})
