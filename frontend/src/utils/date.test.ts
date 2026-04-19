import { describe, it, expect } from "vitest"
import { formatTime, formatDate } from "./date"

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
