import { useSyncExternalStore } from "react"

// A single module-level tick shared by every subscriber — relative-time
// labels across hundreds of cards are all driven from one setInterval.

const TICK_MS = 60_000
const listeners = new Set<() => void>()
let tick = Date.now()
let timer: ReturnType<typeof setInterval> | null = null

function ensureTicking() {
  if (timer != null) return
  timer = setInterval(() => {
    tick = Date.now()
    for (const l of listeners) l()
  }, TICK_MS)
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  ensureTicking()
  return () => {
    listeners.delete(listener)
    if (listeners.size === 0 && timer != null) {
      clearInterval(timer)
      timer = null
    }
  }
}

function getSnapshot(): number {
  return tick
}

export function useNow(): Date {
  const ms = useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
  return new Date(ms)
}
