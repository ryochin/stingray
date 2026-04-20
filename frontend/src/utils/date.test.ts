import { describe, it, expect } from "vitest"
import { formatTime, formatDate, formatRelative } from "./date"

// These formatters always render in Asia/Tokyo, independent of the host TZ.
// ICU zone data must be present (Node 18+).

describe("formatTime", () => {
  it("renders MM/DD HH:mm in JST", () => {
    // 2024-01-02T00:00:00Z = 2024-01-02 09:00 JST
    const out = formatTime("2024-01-02T00:00:00Z")
    expect(out).toMatch(/01\/02/)
    expect(out).toMatch(/09:00/)
  })

  it("wraps midnight UTC to next-day in JST", () => {
    // 2024-06-15T15:30:00Z = 2024-06-16 00:30 JST
    const out = formatTime("2024-06-15T15:30:00Z")
    expect(out).toMatch(/06\/16/)
    expect(out).toMatch(/00:30/)
  })
})

describe("formatDate", () => {
  it("includes year and JST suffix", () => {
    const out = formatDate("2024-01-02T00:00:00Z")
    expect(out).toContain("2024")
    expect(out).toContain("01/02")
    expect(out.endsWith("JST")).toBe(true)
  })
})


describe("formatRelative", () => {
  const now = new Date("2024-06-15T12:00:00Z")
  const sec = (n: number) => new Date(now.getTime() - n * 1000).toISOString()

  it("returns 'just now' within 45s", () => {
    expect(formatRelative(sec(0), now)).toBe("just now")
    expect(formatRelative(sec(44), now)).toBe("just now")
  })

  it("returns 'N min' under an hour", () => {
    expect(formatRelative(sec(60), now)).toBe("1 min")
    expect(formatRelative(sec(32 * 60), now)).toBe("32 min")
    expect(formatRelative(sec(60 * 59), now)).toBe("59 min")
  })

  it("returns 'N hr' under a day (no pluralization)", () => {
    expect(formatRelative(sec(3600), now)).toBe("1 hr")
    expect(formatRelative(sec(13 * 3600), now)).toBe("13 hr")
  })

  it("returns 'N day(s) M hr' for 24h..72h", () => {
    expect(formatRelative(sec(86400), now)).toBe("1 day 0 hr")
    expect(formatRelative(sec(86400 + 3 * 3600), now)).toBe("1 day 3 hr")
    expect(formatRelative(sec(2 * 86400 + 11 * 3600), now)).toBe("2 days 11 hr")
    // 3 days exactly → falls into the simple-days branch below.
    expect(formatRelative(sec(3 * 86400), now)).toBe("3 days")
  })

  it("returns 'N days' for 3d..7d", () => {
    expect(formatRelative(sec(6 * 86400), now)).toBe("6 days")
  })

  it("returns 'N week(s)' under 4 weeks", () => {
    expect(formatRelative(sec(7 * 86400), now)).toBe("1 week")
    expect(formatRelative(sec(14 * 86400), now)).toBe("2 weeks")
  })

  it("returns 'N month(s)' under ~6 months", () => {
    // 28d (4w) exactly → falls into months branch since weeks branch is < 28d.
    expect(formatRelative(sec(28 * 86400), now)).toBe("1 month")
    // ~5 months
    expect(formatRelative(sec(150 * 86400), now)).toBe("5 months")
  })

  it("falls back to absolute for older than ~6 months", () => {
    const old = new Date(now.getTime() - 200 * 86400 * 1000).toISOString()
    expect(formatRelative(old, now)).toContain("JST")
  })
})
