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
