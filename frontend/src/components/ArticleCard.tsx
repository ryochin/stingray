import { forwardRef, useMemo } from "react"
import DOMPurify from "dompurify"
import type { Article } from "../api/client"

function parseSummary(summary: string): { text: string, imageUrls: string[] } {
  const imageUrls: string[] = []
  const text = summary.replace(/<image>([\s\S]*?)<\/image>/g, (_match, url) => {
    imageUrls.push(url.trim())
    return ""
  })
  return { text: text.trim(), imageUrls }
}

function domain(url: string): string {
  try {
    return new URL(url).hostname
  } catch {
    return ""
  }
}

function formatDate(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleString("ja-JP", {
    timeZone: "Asia/Tokyo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }) + " JST"
}

interface Props {
  article: Article
  focused?: boolean
  pendingSummary?: boolean
  onClick?: () => void
}

const ArticleCard = forwardRef<HTMLDivElement, Props>(
  ({ article, focused, pendingSummary, onClick }, ref) => {
    const isRead = article.read_at != null
    const hasTranslation = article.title_ja && article.title_ja !== article.title
    const titleColor = focused ? "text-accent-text" : isRead ? "text-text-muted" : "text-text-heading"
    const parsedSummary = useMemo(
      () => article.summary ? parseSummary(article.summary) : null,
      [article.summary],
    )
    const sanitizedHtml = useMemo(() => {
      if (!article.content_html) return null
      const clean = DOMPurify.sanitize(article.content_html)
      const doc = new DOMParser().parseFromString(clean, "text/html")
      const seen = new Set<string>()
      for (const img of doc.querySelectorAll("img")) {
        const src = img.getAttribute("src")
        if (!src) continue
        if (seen.has(src)) {
          img.closest("a")?.remove() ?? img.remove()
        } else {
          seen.add(src)
        }
      }
      return doc.body.innerHTML
    }, [article.content_html])

    return (
      <div
        ref={ref}
        onClick={onClick}
        className={`py-5 px-6 rounded-lg mb-4 border cursor-pointer transition-colors ${
          focused
            ? "bg-bg-hover border-accent-bg"
            : isRead
              ? "bg-bg-secondary border-border opacity-60"
              : "bg-bg-secondary border-border hover:border-text-dim"
        }`}
      >
        {hasTranslation ? (
          <>
            <div className={`${titleColor} font-medium text-[16pt] leading-snug`}>
              <a
                href={article.url}
                target="_blank"
                rel="noopener noreferrer"
                className={`${titleColor} hover:underline no-underline`}
              >
                {article.title_ja}
              </a>
            </div>
            <div className="mt-0.5">
              <a
                href={article.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-sm text-link hover:text-link-hover hover:underline no-underline"
              >
                {article.title}
              </a>
              <span className="text-xs text-text-dim ml-1">({domain(article.url)})</span>
            </div>
          </>
        ) : (
          <div className={`${titleColor} font-medium text-[16pt] leading-snug`}>
            <a
              href={article.url}
              target="_blank"
              rel="noopener noreferrer"
              className={`${titleColor} hover:underline no-underline`}
            >
              {article.title}
            </a>
            <span className="text-xs text-text-dim ml-1">({domain(article.url)})</span>
          </div>
        )}

        {article.published && (
          <div className="text-sm text-text-muted mt-0.5">
            {formatDate(article.published)}
          </div>
        )}

        {!parsedSummary && pendingSummary && (
          <div className="mt-1 text-sm text-text-dim italic">Awaiting summary...</div>
        )}

        {parsedSummary && (
          <div className={`mt-2 pl-3 border-l-2 border-border ${isRead ? "text-text-muted" : "text-text"}`}>
            {parsedSummary.text && <p>{parsedSummary.text}</p>}
            {!sanitizedHtml && parsedSummary.imageUrls.length > 0 && (
              <div className="mt-2 space-y-2">
                {parsedSummary.imageUrls.map((url, i) => (
                  <img
                    key={i}
                    src={url}
                    alt=""
                    className="max-w-full h-auto rounded border border-border"
                    loading="lazy"
                  />
                ))}
              </div>
            )}
          </div>
        )}

        {sanitizedHtml && (
          <div
            className="mt-2 text-base text-text article-html prose prose-invert max-w-none"
            dangerouslySetInnerHTML={{ __html: sanitizedHtml }}
          />
        )}
      </div>
    )
  },
)

ArticleCard.displayName = "ArticleCard"

export default ArticleCard
