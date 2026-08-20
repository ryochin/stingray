import { describe, expect, it } from "vitest"
import { formatDate, formatRelative, formatTime } from "./date"

// These formatters follow the browser's locale and timezone. test-timezone.ts
// pins TZ=UTC, but the locale cannot be pinned from inside Node — ICU reads it
// once at init and later LC_ALL reassignment has no effect. So these assertions
// are locale-tolerant: they check the 24-hour time, which is locale-invariant
// under hourCycle "h23", and the day, which is read back through Intl rather
// than hard-coded. What they never check is the order of the date components,
// which is Intl's business rather than ours.

// Throws rather than returning a fallback: a silently empty expectation would
// make every `toContain` below pass vacuously.
function expectedDay(iso: string): string {
  const parts: Intl.DateTimeFormatPart[] = new Intl.DateTimeFormat(undefined, {
    day: "2-digit",
  }).formatToParts(new Date(iso))
  const day: Intl.DateTimeFormatPart | undefined = parts.find(
    (p: Intl.DateTimeFormatPart) => p.type === "day",
  )
  if (!day?.value) throw new Error(`no day part for ${iso}`)
  return day.value
}

describe("formatTime", () => {
  it("renders the time in 24-hour form", () => {
    const out: string = formatTime("2024-01-02T00:00:00Z")
    expect(out).toContain("00:00")
  })

  it("keeps date and time paired across a midnight boundary", () => {
    const before: string = "2024-06-15T23:30:00Z"
    const after: string = "2024-06-16T00:30:00Z"
    expect(formatTime(before)).toContain("23:30")
    expect(formatTime(before)).toContain(expectedDay(before))
    expect(formatTime(after)).toContain("00:30")
    expect(formatTime(after)).toContain(expectedDay(after))
    // Keeps the fixtures honest: east of UTC these two instants fall on the
    // same day, and the day assertions above would then compare a day to
    // itself rather than to the one across the boundary.
    expect(expectedDay(before)).not.toBe(expectedDay(after))
  })
})

describe("formatDate", () => {
  it("includes the year and a timezone name", () => {
    const out: string = formatDate("2024-01-02T00:00:00Z")
    expect(out).toContain("2024")
    expect(out).toContain("00:00")
    expect(out).toMatch(/UTC|GMT/)
  })
})

describe("formatRelative", () => {
  const now: Date = new Date("2024-06-15T12:00:00Z")
  const sec = (n: number): string =>
    new Date(now.getTime() - n * 1000).toISOString()

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
    const old: string = new Date(
      now.getTime() - 200 * 86400 * 1000,
    ).toISOString()
    // 200 days before 2024-06-15 lands in 2023, so this asserts the fallback
    // to an absolute date without coupling to any format.
    expect(formatRelative(old, now)).toContain("2023")
  })
})
