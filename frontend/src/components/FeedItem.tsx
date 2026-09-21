import type { JSX } from "react"
import type { Feed, FeedStats, Folder } from "../api/client"
import { faviconUrl } from "../api/client"
import type { DragHandlers } from "../hooks/useFeedDnD"
import { formatRelativeShort } from "../utils/date"
import { setCardDragImage } from "../utils/dragImage"
import FeedDetails from "./FeedDetails"

/** Per-feed side effects. Grouped so a row does not take a dozen
 *  individually-named callbacks, and so the list can pass them straight
 *  through without re-declaring each one. */
interface FeedItemActions {
  onFetch: (feedId: number) => void
  onDelete: (feed: Feed) => void
  onToggleEnabled: (feedId: number) => void
  onToggleSummarize: (feedId: number) => void
  onMoveToFolder: (feedId: number, folderId: number | null) => void
  onToggleTranslate: (feedId: number, translate: boolean) => void
  onUpdateSiteUrl: (feedId: number, siteUrl: string | null) => void
  onRulesUpdated: () => void
}

/** Inline-rename state. `id` is the feed being renamed anywhere in the list
 *  (null when none), so this object is identical for every row. */
interface EditingState {
  id: number | null
  name: string
  onStart: (feed: Feed) => void
  onCancel: () => void
  onCommit: (feed: Feed, name: string) => void
  onChange: (name: string) => void
}

interface ExpandedState {
  open: boolean
  onToggle: (feedId: number) => void
}

type FeedItemProps = {
  feed: Feed
  stats?: FeedStats
  folders: Folder[]
  fetching: boolean
  editing: EditingState
  expanded: ExpandedState
  dnd: DragHandlers & { dragOver: boolean }
  actions: FeedItemActions
}

function FeedItem({
  feed,
  stats,
  folders,
  fetching,
  editing,
  expanded,
  dnd,
  actions,
}: FeedItemProps): JSX.Element {
  const isEditing: boolean = editing.id === feed.id
  const isUnhealthy: boolean = feed.consecutive_failures >= 3
  const favicon: string | null = faviconUrl(feed)
  const hasRules: boolean = feed.extraction_rules !== "{}"
  const isWebFeed: boolean = feed.extraction_rules != null

  return (
    // biome-ignore lint/a11y/useSemanticElements: switching to <li> would require restructuring the parent FeedList container into <ul>
    <div
      role="listitem"
      onDragOver={(event: React.DragEvent<HTMLDivElement>): void => {
        event.preventDefault()
        dnd.onDragOver(feed)
      }}
      onDragLeave={(): void => dnd.onDragLeave(feed)}
      onDrop={(event: React.DragEvent<HTMLDivElement>): void => {
        event.preventDefault()
        dnd.onDrop(feed)
      }}
      className={`bg-bg-secondary rounded-lg border transition-colors ${
        dnd.dragOver ? "border-accent border-solid" : "border-border"
      }`}
    >
      <div className="flex items-center justify-between p-3">
        <div className="flex-1 min-w-0 flex items-start gap-2.5">
          {/* biome-ignore lint/a11y/noStaticElementInteractions: drag handle relies on native HTML5 DnD, which has no standard ARIA role */}
          <span
            draggable={!isEditing}
            onDragStart={(event: React.DragEvent<HTMLSpanElement>): void => {
              setCardDragImage(event, '[role="listitem"]')
              dnd.onDragStart(feed)
            }}
            onDragEnd={dnd.onDragEnd}
            className={`text-text-dim select-none mt-0.5 shrink-0 inline-flex px-1 -mx-1 ${
              isEditing
                ? "cursor-default opacity-40"
                : "cursor-grab active:cursor-grabbing"
            }`}
            title={isEditing ? undefined : "Drag to reorder"}
          >
            ⠿
          </span>
          {favicon && (
            <img
              src={favicon}
              alt=""
              className="w-4 h-4 shrink-0 mt-1"
              loading="lazy"
            />
          )}
          <div className="flex-1 min-w-0">
            {isEditing ? (
              <input
                value={editing.name}
                onChange={(event: React.ChangeEvent<HTMLInputElement>): void =>
                  editing.onChange(event.target.value)
                }
                onKeyDown={(
                  event: React.KeyboardEvent<HTMLInputElement>,
                ): void => {
                  if (event.key === "Enter" && editing.name.trim()) {
                    editing.onCommit(feed, editing.name.trim())
                  }
                  if (event.key === "Escape") editing.onCancel()
                }}
                onBlur={(): void => {
                  if (
                    editing.name.trim() &&
                    editing.name.trim() !== feed.name
                  ) {
                    editing.onCommit(feed, editing.name.trim())
                  } else {
                    editing.onCancel()
                  }
                }}
                className="bg-bg-card text-text border border-border rounded px-2 py-0.5 text-sm font-medium w-full"
              />
            ) : (
              // biome-ignore lint/a11y/useSemanticElements: cannot use <button> because the toggle area contains a nested Rename <button> (interactive content cannot nest per HTML spec)
              <div
                role="button"
                tabIndex={0}
                className="flex items-center gap-1.5 cursor-pointer"
                onClick={(): void => expanded.onToggle(feed.id)}
                onKeyDown={(
                  event: React.KeyboardEvent<HTMLDivElement>,
                ): void => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault()
                    expanded.onToggle(feed.id)
                  }
                }}
              >
                <span className="text-text-dim text-xs shrink-0">
                  {expanded.open ? "\u25BE" : "\u25B8"}
                </span>
                {isWebFeed && (
                  <span
                    className={`text-xs px-1 rounded shrink-0 ${
                      hasRules
                        ? "text-accent-text bg-accent-bg"
                        : "text-yellow-400 bg-yellow-400/10 border border-yellow-400/30 border-solid"
                    }`}
                    title={
                      hasRules
                        ? "Web page feed"
                        : "Web page feed — rules not configured"
                    }
                  >
                    WEB{hasRules ? "" : " ⚠"}
                  </span>
                )}
                {isUnhealthy && (
                  <span
                    className="text-yellow-400 text-sm shrink-0"
                    title={`${feed.consecutive_failures} consecutive failures${feed.last_error ? `: ${feed.last_error}` : ""}`}
                  >
                    !!!
                  </span>
                )}
                <span
                  className={`font-medium ${feed.enabled ? "text-text-heading" : "text-text-dim line-through"}`}
                >
                  {feed.name}
                </span>
                <button
                  type="button"
                  onClick={(
                    event: React.MouseEvent<HTMLButtonElement>,
                  ): void => {
                    event.stopPropagation()
                    editing.onStart(feed)
                  }}
                  className="text-text-dim hover:text-text transition-colors text-xs"
                  title="Rename"
                >
                  ✎
                </button>
              </div>
            )}
            <div className="text-xs text-text-muted mt-0.5">
              <a
                href={feed.site_url ?? feed.url ?? ""}
                target="_blank"
                rel="noopener noreferrer"
                className="text-link hover:text-link-hover hover:underline"
              >
                {feed.site_url ?? feed.url}
              </a>
              {feed.site_url && feed.url && (
                <a
                  href={feed.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-text-dim hover:text-link-hover ml-1"
                  title={feed.url}
                >
                  &#8853;
                </a>
              )}
              {feed.last_fetched_at && (
                <> &middot; {formatRelativeShort(feed.last_fetched_at)}</>
              )}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2 ml-4 shrink-0">
          <button
            type="button"
            onClick={(): void => actions.onToggleSummarize(feed.id)}
            className={`px-2 py-1 rounded text-xs border transition-colors ${
              feed.summarize
                ? "bg-accent-bg text-accent-text border-accent-bg"
                : "bg-bg-card text-text-muted border-border hover:text-text"
            }`}
            title="Summarize articles from this feed"
          >
            Summarize
          </button>
          <select
            value={feed.folder_id ?? ""}
            onChange={(event: React.ChangeEvent<HTMLSelectElement>): void =>
              actions.onMoveToFolder(
                feed.id,
                event.target.value ? Number(event.target.value) : null,
              )
            }
            className="bg-bg-card text-text-muted border border-border rounded px-1.5 py-1 text-xs"
          >
            <option value="">--</option>
            {folders.map((folder: Folder) => (
              <option key={folder.id} value={folder.id}>
                {folder.name}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={(): void => actions.onFetch(feed.id)}
            disabled={fetching}
            title="Fetch now"
            className="px-2 py-1 rounded text-xs bg-bg-card text-text-muted hover:text-text transition-colors disabled:opacity-40"
          >
            <span className={`inline-block ${fetching ? "animate-spin" : ""}`}>
              ↻
            </span>
          </button>
          <button
            type="button"
            onClick={(): void => actions.onToggleEnabled(feed.id)}
            className={`px-2 py-1 rounded text-xs transition-colors ${
              feed.enabled
                ? "bg-green-900 text-green-300"
                : "bg-bg-card text-text-dim"
            }`}
          >
            {feed.enabled ? "ON" : "OFF"}
          </button>
        </div>
      </div>

      {expanded.open && (
        <FeedDetails
          feed={feed}
          stats={stats}
          onDelete={(): void => actions.onDelete(feed)}
          onToggleTranslate={(): void =>
            actions.onToggleTranslate(feed.id, !feed.translate)
          }
          onUpdateSiteUrl={(siteUrl: string | null): void =>
            actions.onUpdateSiteUrl(feed.id, siteUrl)
          }
          onRulesUpdated={isWebFeed ? actions.onRulesUpdated : undefined}
        />
      )}
    </div>
  )
}

export type { EditingState, FeedItemActions }
export default FeedItem
