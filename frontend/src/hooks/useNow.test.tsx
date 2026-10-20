/**
 * `useNow` keeps its tick, timer and listeners at module level, so each test
 * re-imports a fresh copy after `vi.resetModules()`.
 */

import { act, cleanup, renderHook } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const START: Date = new Date("2026-10-20T00:00:00Z")

type UseNow = () => Date

async function loadUseNow(): Promise<UseNow> {
  vi.resetModules()
  const mod: typeof import("./useNow") = await import("./useNow")
  return mod.useNow
}

beforeEach((): void => {
  vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"] })
  vi.setSystemTime(START)
})

afterEach((): void => {
  cleanup()
  vi.useRealTimers()
})

describe("useNow", (): void => {
  it("starts at the time the module was loaded", async (): Promise<void> => {
    const useNow: UseNow = await loadUseNow()
    vi.setSystemTime(new Date(START.getTime() + 5_000))

    const { result } = renderHook(useNow)
    expect(result.current.getTime()).toBe(START.getTime())
  })

  it("advances once per minute, every minute", async (): Promise<void> => {
    const useNow: UseNow = await loadUseNow()
    const { result } = renderHook(useNow)

    act((): void => {
      vi.advanceTimersByTime(59_000)
    })
    expect(result.current.getTime()).toBe(START.getTime())

    act((): void => {
      vi.advanceTimersByTime(1_000)
    })
    expect(result.current.getTime()).toBe(START.getTime() + 60_000)

    act((): void => {
      vi.advanceTimersByTime(60_000)
    })
    expect(result.current.getTime()).toBe(START.getTime() + 120_000)
  })

  it("shares one timer across subscribers", async (): Promise<void> => {
    const useNow: UseNow = await loadUseNow()
    const a = renderHook(useNow)
    const b = renderHook(useNow)
    expect(vi.getTimerCount()).toBe(1)

    act((): void => {
      vi.advanceTimersByTime(60_000)
    })
    expect(a.result.current.getTime()).toBe(START.getTime() + 60_000)
    expect(b.result.current.getTime()).toBe(START.getTime() + 60_000)

    act((): void => {
      vi.advanceTimersByTime(60_000)
    })
    expect(a.result.current.getTime()).toBe(START.getTime() + 120_000)
    expect(b.result.current.getTime()).toBe(START.getTime() + 120_000)
  })

  it("stops the timer once the last subscriber unmounts, and restarts on remount", async (): Promise<void> => {
    const useNow: UseNow = await loadUseNow()
    const a = renderHook(useNow)
    const b = renderHook(useNow)

    a.unmount()
    expect(vi.getTimerCount()).toBe(1)
    b.unmount()
    expect(vi.getTimerCount()).toBe(0)

    const { result } = renderHook(useNow)
    expect(vi.getTimerCount()).toBe(1)
    act((): void => {
      vi.advanceTimersByTime(60_000)
    })
    expect(result.current.getTime()).toBe(START.getTime() + 60_000)
  })
})
