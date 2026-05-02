// Custom rAF smooth scroll. Native `behavior: "smooth"` is not used because
// it can race with TanStack Virtual's scrollAdjustment writes, producing
// visible jitter when item sizes are measured during the animation.

interface RafHandle {
  current: number | null
}

interface Options {
  duration?: number
  // Caller-owned ref so a single source of truth governs cancellation —
  // unmount cleanups and concurrent callers can preempt each other safely.
  rafRef: RafHandle
}

export function smoothScrollTo(
  scrollEl: HTMLElement,
  target: number,
  { duration = 150, rafRef }: Options,
): void {
  // Reclaim the shared raf slot first. If we early-returned on a sub-pixel
  // distance while a previous animation was still running, the old rAF
  // would keep mutating scrollTop on top of the caller's expected no-op.
  if (rafRef.current != null) {
    cancelAnimationFrame(rafRef.current)
    rafRef.current = null
  }
  const start: number = scrollEl.scrollTop
  const distance: number = target - start
  if (Math.abs(distance) < 1) return
  const t0: number = performance.now()
  const step = (now: number): void => {
    const p: number = Math.min(1, (now - t0) / duration)
    const eased: number = 1 - Math.pow(1 - p, 3) // ease-out cubic
    scrollEl.scrollTop = start + distance * eased
    if (p < 1) {
      rafRef.current = requestAnimationFrame(step)
    } else {
      rafRef.current = null
    }
  }
  rafRef.current = requestAnimationFrame(step)
}
