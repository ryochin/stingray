import { forwardRef, useMemo } from "react"
import DOMPurify from "dompurify"
import type { Article } from "../api/client"

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
  onClick?: () => void
}

const ArticleCard = forwardRef<HTMLDivElement, Props>(
  ({ article, focused, onClick }, ref) => {
    const isRead = article.read_at != null
    const hasTranslation = article.title_ja && article.title_ja !== article.title
    const titleColor = focused ? "text-accent-text" : isRead ? "text-text-muted" : "text-text-heading"
    const sanitizedHtml = useMemo(
      () => article.content_html ? DOMPurify.sanitize(article.content_html) : null,
      [article.content_html],
    )

    return (
      <div
        ref={ref}
        onClick={onClick}
        className={`py-3 px-4 rounded-lg mb-2 border-l-2 border cursor-pointer transition-colors ${
          focused
            ? "bg-bg-hover border-accent border-l-accent"
            : isRead
              ? "bg-bg-secondary border-border border-l-transparent opacity-60"
              : "bg-bg-secondary border-border border-l-accent hover:border-text-dim"
        }`}
      >
        {hasTranslation ? (
          <>
            <div className={`${titleColor} font-medium text-2xl leading-snug`}>
              {article.title_ja}
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
          <div className={`${titleColor} font-medium text-2xl leading-snug`}>
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

        {article.summary && (
          <div className={`mt-1 ${isRead ? "text-text-muted" : "text-text"}`}>
            {article.summary}
          </div>
        )}

        {sanitizedHtml && (
          <div
            className="mt-2 text-sm text-text-muted article-html prose prose-invert prose-sm max-w-none"
            dangerouslySetInnerHTML={{ __html: sanitizedHtml }}
          />
        )}
      </div>
    )
  },
)

ArticleCard.displayName = "ArticleCard"

export default ArticleCard
