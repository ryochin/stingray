// Pure text-processing helpers for article display. No DOM / React dependency.

// Parse a summary string that may contain `<image>url</image>` markers,
// returning the text with markers stripped and the captured URLs.
export function parseSummary(summary: string): { text: string, imageUrls: string[] } {
  const imageUrls: string[] = []
  const text: string = summary.replace(/<image>([\s\S]*?)<\/image>/g, (_match: string, url: string): string => {
    imageUrls.push(url.trim())
    return ""
  })
  return { text: text.trim(), imageUrls }
}
