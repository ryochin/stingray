import { forwardRef } from "react"
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
    const hasTranslation = article.title_ja && article.title_ja !== article.title
    const titleColor = focused ? "text-accent-text" : "text-text-heading"

    return (
      <div
        ref={ref}
        onClick={onClick}
        className={`py-3 px-4 rounded-lg mb-2 border cursor-pointer transition-colors ${
          focused
            ? "bg-bg-hover border-accent"
            : "bg-bg-secondary border-border hover:border-text-dim"
        }`}
      >
        {hasTranslation ? (
          <>
            <div className={`${titleColor} font-medium text-lg leading-snug`}>
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
          <div className={`${titleColor} font-medium text-lg leading-snug`}>
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

        {article.content_snippet && article.content_snippet !== article.summary && (
          <div className="mt-1 text-sm text-text-muted line-clamp-3">
            {article.content_snippet}
          </div>
        )}

        {article.summary && (
          <div className="mt-1 text-text">
            {article.summary}
          </div>
        )}
      </div>
    )
  },
)

ArticleCard.displayName = "ArticleCard"

export default ArticleCard
