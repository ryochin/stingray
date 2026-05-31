import DOMPurify from "dompurify"
import type { JSX, KeyboardEvent, MouseEvent } from "react"
import { forwardRef, useMemo } from "react"
import type { Article } from "../api/client"
import { parseSummary } from "../utils/articleContent"
import { formatDate, formatRelative } from "../utils/date"

type ParsedSummary = { text: string; imageUrls: string[] }

import { useNow } from "../hooks/useNow"
import { transformTwitterBlockquotes } from "../utils/twitterCard"

interface Props {
  article: Article
  focused?: boolean
  pendingSummary?: boolean
  feedName?: string
  feedFaviconUrl?: string | null
  onClick?: () => void
  /** Fires when any of the title links is clicked. The link still opens in
   *  a new tab via the browser's default action; the parent uses this hook
   *  to schedule the article as read. */
  onTitleClick?: () => void
}

const ArticleCard = forwardRef<HTMLDivElement, Props>(
  (
    {
      article,
      focused,
      pendingSummary,
      feedName,
      feedFaviconUrl,
      onClick,
      onTitleClick,
    },
    ref,
  ): JSX.Element => {
    const now: Date = useNow()
    const isRead: boolean = article.read_at != null
    // Title-link click handlers. We stop propagation so the wrapper card's
    // onClick (focus + previous-article scheduleRead) doesn't fire — opening
    // the article in a new tab (e.g. Cmd/Ctrl-click for a background tab)
    // shouldn't change the current tab's focus or read state. The browser's
    // default action (new tab) is preserved by *not* calling preventDefault.
    const handleTitleClick = (e: MouseEvent<HTMLAnchorElement>): void => {
      e.stopPropagation()
      onTitleClick?.()
    }
    // Middle-click fires `auxclick`, not `click`. Wire the same hook so a
    // middle-click background-open also marks the article read.
    const handleTitleAuxClick = (e: MouseEvent<HTMLAnchorElement>): void => {
      if (e.button !== 1) return
      e.stopPropagation()
      onTitleClick?.()
    }
    const hasTranslation: boolean =
      !!article.title_translated && article.title_translated !== article.title
    const titleColor: string = focused
      ? "text-accent-text"
      : isRead
        ? "text-text-muted"
        : "text-text-heading"
    const parsedSummary = useMemo<ParsedSummary | null>(
      (): ParsedSummary | null =>
        article.summary ? parseSummary(article.summary) : null,
      [article.summary],
    )
    const sanitizedHtml = useMemo<string | null>((): string | null => {
      if (!article.content_html) return null
      const clean: string = DOMPurify.sanitize(article.content_html)
      const doc: Document = new DOMParser().parseFromString(clean, "text/html")
      transformTwitterBlockquotes(doc)
      const seen: Set<string> = new Set<string>()
      // Strip dimension/layout attrs so the CSS `max-width: 100%` clamp can
      // win. Inline `style="width: ..."` and legacy `align="left"` (seen on
      // AssistOn RSS) otherwise let large feed images burst out of the card.
      const STRIP_IMG_ATTRS: readonly string[] = [
        "width",
        "height",
        "style",
        "align",
        "hspace",
        "vspace",
        "border",
      ]
      // Only width-related properties on ancestors — keep unrelated inline
      // styling (margin, padding, text-align, captions, …) intact.
      const ANCESTOR_WIDTH_STYLE_PROPS: readonly string[] = [
        "width",
        "max-width",
        "min-width",
      ]
      for (const img of doc.querySelectorAll("img")) {
        // Clamp widths on image ancestors unconditionally so wrappers like
        // `<div class="wp-caption" style="width: 1290px">` don't burst the
        // card. Run before the `src` early-return so lazy-load images
        // (`data-src` / `srcset`-only, with no resolved `src`) are also
        // covered.
        let ancestor: Element | null = img.parentElement
        while (ancestor && ancestor !== doc.body) {
          ancestor.removeAttribute("width")
          if (ancestor instanceof HTMLElement) {
            for (const prop of ANCESTOR_WIDTH_STYLE_PROPS) {
              ancestor.style.removeProperty(prop)
            }
          }
          ancestor = ancestor.parentElement
        }
        const src: string | null = img.getAttribute("src")
        if (!src) continue
        for (const attr of STRIP_IMG_ATTRS) img.removeAttribute(attr)
        if (seen.has(src)) {
          img.closest("a")?.remove() ?? img.remove()
        } else {
          seen.add(src)
        }
      }
      return doc.body.innerHTML
    }, [article.content_html])

    return (
      // biome-ignore lint/a11y/useSemanticElements: cannot use <button> because the card contains nested <a> links (interactive content cannot nest per HTML spec)
      <div
        ref={ref}
        role="button"
        tabIndex={0}
        onClick={onClick}
        onKeyDown={(event: KeyboardEvent<HTMLDivElement>): void => {
          // Space is intentionally not handled here so the browser default
          // (page scroll) or the global feed-jump shortcut can take over.
          if (onClick && event.key === "Enter") {
            event.preventDefault()
            onClick()
          }
        }}
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
            <div
              className={`${titleColor} font-medium text-[16pt] leading-snug`}
            >
              <a
                href={article.url}
                target="_blank"
                rel="noopener noreferrer"
                onClick={handleTitleClick}
                onAuxClick={handleTitleAuxClick}
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
                onClick={handleTitleClick}
                onAuxClick={handleTitleAuxClick}
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
              onClick={handleTitleClick}
              onAuxClick={handleTitleAuxClick}
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
                  <img
                    src={feedFaviconUrl}
                    alt=""
                    className="w-3.5 h-3.5"
                    loading="lazy"
                  />
                )}
                <span>{feedName}</span>
              </span>
            )}
            {feedName && article.published && (
              <span className="text-text-dim">·</span>
            )}
            {article.published && (
              <span title={formatDate(article.published)}>
                {formatRelative(article.published, now)}
              </span>
            )}
          </div>
        )}

        {!parsedSummary && !article.content_translated && pendingSummary && (
          <div className="mt-1 text-sm text-text-dim italic">
            Awaiting summary...
          </div>
        )}

        {parsedSummary && (
          <div
            className={`mt-2 pl-3 border-l-2 border-solid border-accent-bg ${isRead ? "text-text-muted" : "text-text"}`}
          >
            {parsedSummary.text && <p>{parsedSummary.text}</p>}
            {!sanitizedHtml && parsedSummary.imageUrls.length > 0 && (
              <div className="mt-2 space-y-2">
                {parsedSummary.imageUrls.map(
                  (url: string): JSX.Element => (
                    <img
                      key={url}
                      src={url}
                      alt=""
                      className="max-w-full h-auto rounded border border-border"
                      loading="lazy"
                    />
                  ),
                )}
              </div>
            )}
          </div>
        )}

        {article.content_translated && (
          <div
            className={`mt-2 pl-3 border-l-2 border-solid border-accent-bg ${isRead ? "text-text-muted" : "text-text"}`}
          >
            <p>{article.content_translated}</p>
          </div>
        )}

        {!parsedSummary &&
          !article.content_translated &&
          !sanitizedHtml &&
          article.content_snippet && (
            <p
              className={`mt-2 text-sm ${isRead ? "text-text-dim" : "text-text-muted"}`}
            >
              {article.content_snippet}
            </p>
          )}

        {sanitizedHtml && (
          <div
            className="mt-2 text-base text-text article-html max-w-none"
            // biome-ignore lint/security/noDangerouslySetInnerHtml: content is sanitized via DOMPurify before rendering (see useMemo above)
            dangerouslySetInnerHTML={{ __html: sanitizedHtml }}
          />
        )}
      </div>
    )
  },
)

ArticleCard.displayName = "ArticleCard"

export default ArticleCard
