import { forwardRef, useMemo } from "react"
import DOMPurify from "dompurify"
import type { Article } from "../api/client"
import { formatDate, formatRelative } from "../utils/date"
import { parseSummary } from "../utils/articleContent"
import { transformTwitterBlockquotes } from "../utils/twitterCard"
import { useNow } from "../hooks/useNow"

interface Props {
  article: Article
  focused?: boolean
  pendingSummary?: boolean
  feedName?: string
  feedFaviconUrl?: string | null
  onClick?: () => void
}

const ArticleCard = forwardRef<HTMLDivElement, Props>(
  ({ article, focused, pendingSummary, feedName, feedFaviconUrl, onClick }, ref) => {
    const now = useNow()
    const isRead = article.read_at != null
    const hasTranslation = article.title_translated && article.title_translated !== article.title
    const titleColor = focused ? "text-accent-text" : isRead ? "text-text-muted" : "text-text-heading"
    const parsedSummary = useMemo(
      () => article.summary ? parseSummary(article.summary) : null,
      [article.summary],
    )
    const sanitizedHtml = useMemo(() => {
      if (!article.content_html) return null
      const clean = DOMPurify.sanitize(article.content_html)
      const doc = new DOMParser().parseFromString(clean, "text/html")
      transformTwitterBlockquotes(doc)
      const seen = new Set<string>()
      // Strip dimension/layout attrs so the CSS `max-width: 100%` clamp can
      // win. Inline `style="width: ..."` and legacy `align="left"` (seen on
      // AssistOn RSS) otherwise let large feed images burst out of the card.
      const STRIP_IMG_ATTRS = ["width", "height", "style", "align", "hspace", "vspace", "border"]
      for (const img of doc.querySelectorAll("img")) {
        const src = img.getAttribute("src")
        if (!src) continue
        STRIP_IMG_ATTRS.forEach((a) => img.removeAttribute(a))
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
        className={`py-5 px-6 rounded-lg border cursor-pointer transition-all duration-500 ${
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
                {article.title_translated}
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
          </div>
        )}

        {(article.published || feedName) && (
          <div className="text-sm text-text-muted mt-2 flex items-center gap-2">
            {feedName && (
              <span className="flex items-center gap-1">
                {feedFaviconUrl && (
                  <img src={feedFaviconUrl} alt="" className="w-3.5 h-3.5" loading="lazy" />
                )}
                <span>{feedName}</span>
              </span>
            )}
            {feedName && article.published && <span className="text-text-dim">·</span>}
            {article.published && (
              <span title={formatDate(article.published)}>{formatRelative(article.published, now)}</span>
            )}
          </div>
        )}

        {!parsedSummary && !article.content_translated && pendingSummary && (
          <div className="mt-1 text-sm text-text-dim italic">Awaiting summary...</div>
        )}

        {parsedSummary && (
          <div className={`mt-2 pl-3 border-l-2 border-solid border-accent-bg ${isRead ? "text-text-muted" : "text-text"}`}>
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

        {article.content_translated && (
          <div className={`mt-2 pl-3 border-l-2 border-solid border-accent-bg ${isRead ? "text-text-muted" : "text-text"}`}>
            <p>{article.content_translated}</p>
          </div>
        )}

        {!parsedSummary && !article.content_translated && !sanitizedHtml && article.content_snippet && (
          <p className={`mt-2 text-sm ${isRead ? "text-text-dim" : "text-text-muted"}`}>
            {article.content_snippet}
          </p>
        )}

        {sanitizedHtml && (
          <div
            className="mt-2 text-base text-text article-html max-w-none"
            dangerouslySetInnerHTML={{ __html: sanitizedHtml }}
          />
        )}
      </div>
    )
  },
)

ArticleCard.displayName = "ArticleCard"

export default ArticleCard
