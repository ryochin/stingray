import type { VirtualItem, Virtualizer } from "@tanstack/react-virtual"
import type { JSX } from "react"
import type { Article, Feed } from "../api/client"
import { faviconUrl } from "../api/client"
import ArticleCard from "./ArticleCard"
import { CARD_GAP } from "./articleListLayout"
import CaughtUpIndicator from "./CaughtUpIndicator"

interface ArticleListProps {
  articles: Article[]
  focusIndex: number
  feedMap: Map<number, Feed>
  summarizeFeedIds: Set<number>
  virtualizer: Virtualizer<HTMLElement, Element>
  /** Virtualizer index that renders the end-of-list "all caught up" sentinel
   *  instead of an article card. */
  allCaughtUpIndex: number
  setRef: (index: number, el: HTMLDivElement | null) => void
  onCardClick: (index: number) => void
  onTitleClick: (url: string) => void
  caughtUpPulseKey: number
  /** Optional sub-text rendered below the "All caught up" label. The
   *  controller decides when/what to show; the view just renders it. */
  caughtUpSubLabel?: string
  /** Ref-callback attached to the sentinel's outer wrapper so callers can
   *  observe whether it is currently on screen (e.g. for gating the
   *  Space-key shortcut on actual hint visibility). */
  caughtUpRef?: (el: HTMLDivElement | null) => void
}

/** Virtualized renderer for the article list. Owns no state of its own —
 *  the virtualizer instance, focus index, and click handlers are all
 *  supplied by the route via `useArticleListController`. Kept as a pure
 *  view layer so the orchestration logic stays in the controller hook. */
export default function ArticleList({
  articles,
  focusIndex,
  feedMap,
  summarizeFeedIds,
  virtualizer,
  allCaughtUpIndex,
  setRef,
  onCardClick,
  onTitleClick,
  caughtUpPulseKey,
  caughtUpSubLabel,
  caughtUpRef,
}: ArticleListProps): JSX.Element {
  return (
    <div
      style={{
        // `getTotalSize()` already nets out `scrollMargin`
        // (see virtual-core: `end - scrollMargin + paddingEnd`)
        // so this is exactly `sum(measured)` — the right value
        // for a container whose first card sits at y=0.
        height: virtualizer.getTotalSize(),
        position: "relative",
        width: "100%",
      }}
    >
      {virtualizer.getVirtualItems().map((vi: VirtualItem) => {
        if (vi.index === allCaughtUpIndex) {
          return (
            <div
              key={vi.key}
              ref={virtualizer.measureElement}
              data-index={vi.index}
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                width: "100%",
                transform: `translateY(${vi.start - virtualizer.options.scrollMargin}px)`,
              }}
            >
              <CaughtUpIndicator
                ref={caughtUpRef}
                label="All caught up"
                pulseKey={caughtUpPulseKey}
                subLabel={caughtUpSubLabel}
                className={
                  caughtUpPulseKey > 0 ? "text-text" : "text-text-muted"
                }
              />
            </div>
          )
        }
        const article: Article | undefined = articles[vi.index]
        if (!article) return null
        const feed: Feed | undefined =
          article.feed_id != null ? feedMap.get(article.feed_id) : undefined
        return (
          <div
            key={vi.key}
            ref={virtualizer.measureElement}
            data-index={vi.index}
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              width: "100%",
              paddingBottom: CARD_GAP,
              transform: `translateY(${vi.start - virtualizer.options.scrollMargin}px)`,
            }}
          >
            <ArticleCard
              article={article}
              focused={vi.index === focusIndex}
              pendingSummary={
                !article.summary &&
                !article.content_translated &&
                article.feed_id != null &&
                summarizeFeedIds.has(article.feed_id)
              }
              feedName={feed?.name}
              feedFaviconUrl={feed ? faviconUrl(feed) : null}
              ref={(el: HTMLDivElement | null): void => setRef(vi.index, el)}
              onClick={(): void => onCardClick(vi.index)}
              onTitleClick={(): void => onTitleClick(article.url)}
            />
          </div>
        )
      })}
    </div>
  )
}
