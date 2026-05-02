// Shared date/time formatters. All output uses the Asia/Tokyo timezone.

const TIMEZONE = "Asia/Tokyo"

export function formatTime(iso: string): string {
  return new Date(iso).toLocaleString("ja-JP", {
    timeZone: TIMEZONE,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  })
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleString("ja-JP", {
    timeZone: TIMEZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }) + " JST"
}

// Compact relative time used by the Feeds page badges: "just now", "32m ago",
// "5h ago", "3d ago". Always tops out at days; older timestamps still get the
// "Nd ago" form. Different from `formatRelative` below, which is the longer
// form used in article timestamps.
export function formatRelativeShort(iso: string, now: Date = new Date()): string {
  const diffMs: number = now.getTime() - new Date(iso).getTime()
  const diffMins: number = Math.floor(diffMs / 60000)
  if (diffMins < 1) return "just now"
  if (diffMins < 60) return `${diffMins}m ago`
  const diffHours: number = Math.floor(diffMins / 60)
  if (diffHours < 24) return `${diffHours}h ago`
  const diffDays: number = Math.floor(diffHours / 24)
  return `${diffDays}d ago`
}

// Relative time in a long-ish English form: "just now", "32 min", "5 hr",
// "1 day 3 hr", "6 days", "3 weeks", "5 months". "min" and "hr" are
// abbreviations and do not pluralize; day/week/month do.
// Anything older than ~6 months falls back to the absolute date.
export function formatRelative(iso: string, now: Date = new Date()): string {
  const diffSec: number = Math.floor((now.getTime() - new Date(iso).getTime()) / 1000)
  if (diffSec < 45) return "just now"
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)} min`
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)} hr`
  if (diffSec < 86400 * 3) {
    const d: number = Math.floor(diffSec / 86400)
    const h: number = Math.floor((diffSec % 86400) / 3600)
    return `${d} ${d === 1 ? "day" : "days"} ${h} hr`
  }
  if (diffSec < 86400 * 7) return `${Math.floor(diffSec / 86400)} days`
  if (diffSec < 86400 * 28) {
    const w: number = Math.floor(diffSec / (86400 * 7))
    return `${w} ${w === 1 ? "week" : "weeks"}`
  }
  if (diffSec < 86400 * 182) {
    // The weeks branch ends at 28d; a "month" is ~30d, so clamp to 1 in the
    // 28..30d gap so we never render "0 months".
    const mo: number = Math.max(1, Math.floor(diffSec / (86400 * 30)))
    return `${mo} ${mo === 1 ? "month" : "months"}`
  }
  return formatDate(iso)
}
