import type { JSX } from "react"
import { lazy, Suspense, useEffect, useState } from "react"
import type { Feed, FeedStats } from "../api/client"
import { formatRelativeShort } from "../utils/date"
import ErrorBoundary from "./ErrorBoundary"

// prismjs + react-simple-code-editor are only needed when a feed actually
// has extraction rules to edit; lazy-load them so the Feeds bundle stays slim.
const ExtractionRulesEditor = lazy(() => import("./ExtractionRulesEditor"))

function SiteUrlEditor({
  feed,
  onSave,
}: {
  feed: Feed
  onSave: (siteUrl: string | null) => void
}): JSX.Element {
  const [value, setValue] = useState<string>(feed.site_url ?? "")
  // Absorb server-side updates (e.g. after refetch) without clobbering an
  // in-progress edit: only reset when the persisted value actually changes.
  useEffect((): void => {
    setValue(feed.site_url ?? "")
  }, [feed.site_url])

  const trimmed: string = value.trim()
  const next: string | null = trimmed === "" ? null : trimmed
  const dirty: boolean = next !== (feed.site_url ?? null)

  const commit = (): void => {
    if (dirty) onSave(next)
  }

  return (
    <div className="flex items-center gap-2 mt-2 text-xs">
      <span className="text-text-muted shrink-0">Site URL:</span>
      <input
        type="url"
        value={value}
        onChange={(event: React.ChangeEvent<HTMLInputElement>): void =>
          setValue(event.target.value)
        }
        onKeyDown={(event: React.KeyboardEvent<HTMLInputElement>): void => {
          if (event.key === "Enter") commit()
          if (event.key === "Escape") setValue(feed.site_url ?? "")
        }}
        placeholder="https://example.com/"
        className="flex-1 bg-bg-card text-text border border-border rounded px-2 py-0.5 focus:outline-none focus:border-accent"
      />
      <button
        type="button"
        onClick={commit}
        disabled={!dirty}
        className="px-2 py-0.5 rounded bg-bg-card text-text-muted hover:text-text border border-border disabled:opacity-40 disabled:cursor-not-allowed"
      >
        Save
      </button>
    </div>
  )
}

function FeedDetails({
  feed,
  stats,
  onDelete,
  onToggleTranslate,
  onUpdateSiteUrl,
  onRulesUpdated,
}: {
  feed: Feed
  stats?: FeedStats
  onDelete: () => void
  onToggleTranslate: () => void
  onUpdateSiteUrl: (siteUrl: string | null) => void
  onRulesUpdated?: () => void
}): JSX.Element {
  return (
    <div className="px-3 pb-3 border-t border-border ml-7">
      <SiteUrlEditor feed={feed} onSave={onUpdateSiteUrl} />
      <div className="flex items-center gap-x-4 gap-y-1 text-xs text-text-muted mt-2">
        {stats && (
          <>
            <span>Articles: {stats.article_count}</span>
            <span>Unread: {stats.unread_count}</span>
            {stats.latest_published && (
              <span>Latest: {formatRelativeShort(stats.latest_published)}</span>
            )}
            {stats.oldest_published && (
              <span>Oldest: {formatRelativeShort(stats.oldest_published)}</span>
            )}
          </>
        )}
        <span>Added: {formatRelativeShort(feed.created_at)}</span>
        {feed.last_fetched_at && (
          <span>Last fetched: {formatRelativeShort(feed.last_fetched_at)}</span>
        )}
        {feed.consecutive_failures > 0 && (
          <span className="text-yellow-400">
            Failures: {feed.consecutive_failures}
          </span>
        )}
        <button
          type="button"
          onClick={onToggleTranslate}
          className={`px-1.5 py-0.5 rounded text-xs transition-colors ${
            feed.translate
              ? "text-accent-text"
              : "text-text-dim hover:text-text-muted"
          }`}
          title="Toggle translation for this feed"
        >
          Translate: {feed.translate ? "ON" : "OFF"}
        </button>
        <span className="flex-1" />
        <button
          type="button"
          onClick={onDelete}
          className="px-2 py-1 rounded text-xs bg-bg-card text-red-400 hover:bg-red-900 hover:text-red-200 transition-colors shrink-0"
        >
          Delete
        </button>
      </div>
      {/*
        Degraded (stale cache / web-norules) shows a soft yellow "Stale"
        warning; a hard failure shows red "Error". The backend `health` state
        classifies this explicitly, so even a manual fetch failure — which
        leaves consecutive_failures untouched — is correctly surfaced as red.
      */}
      {feed.health !== "ok" && feed.last_error && (
        <div
          className={`text-xs mt-1 truncate ${
            feed.health === "degraded" ? "text-yellow-400" : "text-red-400"
          }`}
          title={feed.last_error}
        >
          {feed.health === "degraded" ? "Stale" : "Error"}: {feed.last_error}
        </div>
      )}
      {feed.extraction_rules != null && onRulesUpdated && (
        // Suspense only covers the pending state — a chunk that fails to load
        // (stale tab after a deploy) throws during render, as does any bug
        // inside the editor. Either would otherwise take the whole feed list
        // down, so the message stays deliberately vague about the cause.
        // Collapsing the card unmounts the boundary, so re-expanding retries.
        <ErrorBoundary
          fallback={
            <div className="mt-2 p-2 text-xs text-red-400">
              The rules editor is unavailable.
            </div>
          }
        >
          <Suspense
            fallback={
              <div className="mt-2 p-2 text-xs text-text-dim">
                Loading editor...
              </div>
            }
          >
            <ExtractionRulesEditor feed={feed} onSaved={onRulesUpdated} />
          </Suspense>
        </ErrorBoundary>
      )}
    </div>
  )
}

export default FeedDetails
