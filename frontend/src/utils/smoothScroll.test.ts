import { describe, it, expect, beforeEach, vi } from "vitest"
import { smoothScrollTo } from "./smoothScroll"

// Drive rAF synchronously so we can assert intermediate scroll positions
// without leaning on real timing.
type Entry = { handle: number, cb: (t: number) => void }
let rafQueue: Entry[] = []
let nextHandle = 1
let now = 0

function tick(ms: number) {
  now += ms
  const queue = rafQueue
  rafQueue = []
  for (const { cb } of queue) cb(now)
}

beforeEach(() => {
  rafQueue = []
  nextHandle = 1
  now = 0
  vi.stubGlobal("requestAnimationFrame", (cb: (t: number) => void) => {
    const handle = nextHandle++
    rafQueue.push({ handle, cb })
    return handle
  })
  vi.stubGlobal("cancelAnimationFrame", (h: number) => {
    rafQueue = rafQueue.filter((entry) => entry.handle !== h)
  })
  vi.stubGlobal("performance", { now: () => now })
})

function makeScrollEl(initial = 0): HTMLElement {
  const el = document.createElement("div")
  Object.defineProperty(el, "scrollTop", {
    value: initial,
    writable: true,
    configurable: true,
  })
  return el
}

describe("smoothScrollTo", () => {
  it("animates from start to target across rAF ticks", () => {
    const el = makeScrollEl(0)
    const rafRef = { current: null as number | null }
    smoothScrollTo(el, 100, { duration: 150, rafRef })
    expect(rafRef.current).not.toBeNull()
    tick(75)  // halfway
    expect(el.scrollTop).toBeGreaterThan(0)
    expect(el.scrollTop).toBeLessThan(100)
    tick(75)  // finish
    expect(el.scrollTop).toBe(100)
    expect(rafRef.current).toBeNull()
  })

  it("cancels a previous in-flight animation before deciding sub-pixel no-op", () => {
    const el = makeScrollEl(0)
    const rafRef = { current: null as number | null }
    smoothScrollTo(el, 100, { duration: 150, rafRef })
    const firstHandle = rafRef.current
    expect(firstHandle).not.toBeNull()

    // Re-target the same point we'd reach after the half-tick. The new call
    // must cancel the old rAF even though its own distance is < 1px, or the
    // first animation would keep stepping scrollTop forward.
    tick(75)
    smoothScrollTo(el, el.scrollTop, { duration: 150, rafRef })
    expect(rafRef.current).toBeNull()

    // Drain the queue: the old rAF must NOT continue mutating scrollTop.
    const before = el.scrollTop
    tick(1000)
    expect(el.scrollTop).toBe(before)
  })

  it("preempts a previous animation when called with a new target", () => {
    const el = makeScrollEl(0)
    const rafRef = { current: null as number | null }
    smoothScrollTo(el, 200, { duration: 150, rafRef })
    tick(50)
    const handleAfterFirst = rafRef.current
    smoothScrollTo(el, 0, { duration: 150, rafRef })
    expect(rafRef.current).not.toBe(handleAfterFirst)
    tick(150)
    expect(el.scrollTop).toBe(0)
  })
})
