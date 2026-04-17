import { useState, useRef, useCallback } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { api, faviconUrl } from "../api/client"
import type { Feed, Folder, FeedCreate, FeedStats } from "../api/client"
import Header from "../components/Header"

function AddFeedForm({ onAdd, isAdding, folders }: { onAdd: (f: FeedCreate) => void, isAdding: boolean, folders: Folder[] }) {
  const [name, setName] = useState("")
  const [url, setUrl] = useState("")
  const [folderId, setFolderId] = useState<number | undefined>(undefined)

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    const body: FeedCreate = { name, url }
    if (folderId != null) body.folder_id = folderId
    onAdd(body)
  }

  const reset = () => {
    setName("")
    setUrl("")
    setFolderId(undefined)
  }

  return (
    <form onSubmit={submit} onReset={reset} className="flex flex-wrap gap-3 items-end p-4 bg-bg-secondary rounded-lg border border-border mb-6">
      <div className="flex flex-col gap-1 flex-1 min-w-48">
        <label className="text-xs text-text-muted">URL</label>
        <input value={url} onChange={(e) => setUrl(e.target.value)} required
          className="bg-bg-card text-text border border-border rounded px-2 py-1.5 text-sm"
          placeholder="https://example.com/feed" />
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-xs text-text-muted">Name</label>
        <input value={name} onChange={(e) => setName(e.target.value)}
          className="bg-bg-card text-text border border-border rounded px-2 py-1.5 text-sm w-40"
          placeholder="Auto-detect" />
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-xs text-text-muted">Folder</label>
        <select value={folderId ?? ""} onChange={(e) => setFolderId(e.target.value ? Number(e.target.value) : undefined)}
          className="bg-bg-card text-text border border-border rounded px-2 py-1.5 text-sm">
          <option value="">--</option>
          {folders.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
        </select>
      </div>
      <button type="submit" disabled={isAdding}
        className="px-4 py-1.5 rounded bg-accent text-white text-sm hover:opacity-90 transition-opacity disabled:opacity-40">
        {isAdding ? "Adding..." : "Add"}
      </button>
    </form>
  )
}

function FolderManager({ folders, onError }: { folders: Folder[], onError: (e: Error) => void }) {
  const queryClient = useQueryClient()
  const [newName, setNewName] = useState("")
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editName, setEditName] = useState("")
  const dragSrcId = useRef<number | null>(null)
  const [dragOverId, setDragOverId] = useState<number | null>(null)

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["folders"] })
    queryClient.invalidateQueries({ queryKey: ["feeds"] })
  }

  const createFolder = useMutation({
    mutationFn: (name: string) => api.createFolder(name),
    onSuccess: () => { invalidate(); setNewName("") },
    onError,
  })
  const renameFolder = useMutation({
    mutationFn: ({ id, name }: { id: number, name: string }) => api.renameFolder(id, name),
    onSuccess: () => { invalidate(); setEditingId(null) },
    onError,
  })
  const deleteFolder = useMutation({
    mutationFn: api.deleteFolder,
    onSuccess: invalidate,
    onError,
  })

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault()
    if (!newName.trim()) return
    createFolder.mutate(newName.trim())
  }

  const startEdit = (folder: Folder) => {
    setEditingId(folder.id)
    setEditName(folder.name)
  }

  const commitEdit = () => {
    if (editingId == null || !editName.trim()) return
    renameFolder.mutate({ id: editingId, name: editName.trim() })
  }

  const handleDrop = useCallback((targetId: number) => {
    const srcId = dragSrcId.current
    dragSrcId.current = null
    setDragOverId(null)
    if (srcId == null || srcId === targetId) return
    const ids = folders.map((f) => f.id)
    const fromIdx = ids.indexOf(srcId)
    const toIdx = ids.indexOf(targetId)
    if (fromIdx < 0 || toIdx < 0) return
    ids.splice(fromIdx, 1)
    ids.splice(toIdx, 0, srcId)
    api.reorderFolders(ids).then(() => {
      queryClient.invalidateQueries({ queryKey: ["folders"] })
    })
  }, [folders, queryClient])

  return (
    <div className="mb-6">
      <h3 className="text-sm font-semibold text-text-heading mb-3">Folders</h3>
      <form onSubmit={handleCreate} className="flex gap-2 mb-3">
        <input
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          className="bg-bg-card text-text border border-border rounded px-2 py-1.5 text-sm flex-1"
          placeholder="New folder name"
        />
        <button
          type="submit"
          disabled={createFolder.isPending || !newName.trim()}
          className="px-3 py-1.5 rounded bg-accent text-white text-sm hover:opacity-90 transition-opacity disabled:opacity-40"
        >
          Add
        </button>
      </form>
      {folders.length > 0 && (
        <div className="space-y-1">
          {folders.map((folder) => (
            <div
              key={folder.id}
              className={`flex items-center gap-2 p-2 bg-bg-secondary rounded border transition-colors ${
                dragOverId === folder.id ? "border-accent border-solid" : "border-border"
              }`}
              draggable={editingId !== folder.id}
              onDragStart={() => { dragSrcId.current = folder.id }}
              onDragOver={(e) => { e.preventDefault(); setDragOverId(folder.id) }}
              onDragLeave={() => setDragOverId((prev) => prev === folder.id ? null : prev)}
              onDragEnd={() => { dragSrcId.current = null; setDragOverId(null) }}
              onDrop={() => handleDrop(folder.id)}
            >
              <span className="text-text-dim cursor-grab active:cursor-grabbing select-none" title="Drag to reorder">⠿</span>
              {editingId === folder.id ? (
                <>
                  <input
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") commitEdit(); if (e.key === "Escape") setEditingId(null) }}
                    autoFocus
                    className="bg-bg-card text-text border border-border rounded px-2 py-1 text-sm flex-1"
                  />
                  <button onClick={commitEdit}
                    className="px-2 py-1 rounded text-xs bg-accent text-white hover:opacity-90">
                    Save
                  </button>
                  <button onClick={() => setEditingId(null)}
                    className="px-2 py-1 rounded text-xs bg-bg-card text-text-muted hover:text-text">
                    Cancel
                  </button>
                </>
              ) : (
                <>
                  <span className="flex-1 text-sm text-text">{folder.name}</span>
                  <button onClick={() => startEdit(folder)}
                    className="px-2 py-1 rounded text-xs bg-bg-card text-text-muted hover:text-text transition-colors">
                    Rename
                  </button>
                  <button onClick={() => { if (confirm(`Delete folder "${folder.name}"? Feeds will be moved to uncategorized.`)) deleteFolder.mutate(folder.id) }}
                    className="px-2 py-1 rounded text-xs bg-bg-card text-red-400 hover:bg-red-900 hover:text-red-200 transition-colors">
                    Delete
                  </button>
                </>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function OpmlButtons({ onError, onImported, feedCount }: { onError: (e: Error) => void, onImported: () => void, feedCount: number }) {
  const [importResult, setImportResult] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const handleExport = async () => {
    try {
      const blob = await api.exportOpml()
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = "subscriptions.opml"
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      onError(e as Error)
    }
  }

  const handleImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    try {
      const result = await api.importOpml(file)
      setImportResult(
        `Imported: ${result.folders_created} folders, ${result.feeds_created} feeds (${result.feeds_skipped} skipped)`
      )
      onImported()
    } catch (err) {
      onError(err as Error)
    }
    if (fileRef.current) fileRef.current.value = ""
  }

  return (
    <div className="flex items-center gap-2">
      {importResult && (
        <span className="text-xs text-text-muted mr-2">{importResult}</span>
      )}
      <button
        onClick={handleExport}
        disabled={feedCount === 0}
        className="px-3 py-1.5 rounded text-sm bg-bg-card text-text-muted border border-border hover:text-text transition-colors disabled:opacity-40 disabled:pointer-events-none"
      >
        Export OPML
      </button>
      <label className="px-3 py-1.5 rounded text-sm bg-bg-card text-text-muted border border-border hover:text-text transition-colors cursor-pointer">
        Import OPML
        <input
          ref={fileRef}
          type="file"
          accept=".opml,.xml"
          onChange={handleImport}
          className="hidden"
        />
      </label>
    </div>
  )
}

function ExtractionRulesEditor({ feed, onSaved }: { feed: Feed, onSaved: () => void }) {
  const pretty = (() => {
    try {
      const parsed = JSON.parse(feed.extraction_rules || "{}")
      return Object.keys(parsed).length > 0 ? JSON.stringify(parsed, null, 2) : ""
    } catch { return feed.extraction_rules || "" }
  })()
  const [json, setJson] = useState(pretty)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const validate = (): Record<string, string | null> | null => {
    try {
      const parsed = JSON.parse(json)
      if (typeof parsed !== "object" || Array.isArray(parsed)) {
        setError("Must be a JSON object")
        return null
      }
      if (!parsed.item || !parsed.title || !parsed.link) {
        setError("item, title, link are required")
        return null
      }
      return parsed
    } catch {
      setError("Invalid JSON")
      return null
    }
  }

  const handleSave = async () => {
    const parsed = validate()
    if (!parsed) return
    setSaving(true)
    setError(null)
    try {
      await api.updateFeedRules(feed.id, parsed)
      onSaved()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save")
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="mt-2 p-2 bg-bg-card rounded border border-border">
      <div className="text-xs font-semibold text-text-heading mb-1">CSS Extraction Rules</div>
      <div className="text-xs text-text-dim mb-1.5">{"Keys: item*, title*, link*, link_attr, date, date_attr, thumbnail, thumbnail_attr"}</div>
      <textarea
        value={json}
        onChange={(e) => setJson(e.target.value)}
        rows={12}
        spellCheck={false}
        className="bg-bg-secondary text-text border border-border rounded px-2 py-1.5 text-xs font-mono w-full resize-y"
        placeholder='{"item": "p", "title": "b a", "link": "b a", "link_attr": "href"}'
      />
      {error && <div className="text-xs text-red-400 mt-1">{error}</div>}
      <div className="flex justify-end mt-1.5">
        <button
          onClick={handleSave}
          disabled={saving || !json.trim()}
          className="px-3 py-1 rounded text-xs bg-accent text-white hover:opacity-90 transition-opacity disabled:opacity-40"
        >
          {saving ? "Saving..." : "Save Rules"}
        </button>
      </div>
    </div>
  )
}

function FeedDetails({ feed, stats, onDelete, onToggleTranslate, onRulesUpdated }: {
  feed: Feed
  stats?: FeedStats
  onDelete: () => void
  onToggleTranslate: () => void
  onRulesUpdated?: () => void
}) {
  return (
    <div className="px-3 pb-3 border-t border-border ml-7">
      <div className="flex items-center gap-x-4 gap-y-1 text-xs text-text-muted mt-2">
        {stats && (
          <>
            <span>Articles: {stats.article_count}</span>
            <span>Unread: {stats.unread_count}</span>
            {stats.latest_published && (
              <span>Latest: {formatRelativeTime(stats.latest_published)}</span>
            )}
            {stats.oldest_published && (
              <span>Oldest: {formatRelativeTime(stats.oldest_published)}</span>
            )}
          </>
        )}
        <span>Added: {formatRelativeTime(feed.created_at)}</span>
        {feed.last_fetched_at && (
          <span>Last fetched: {formatRelativeTime(feed.last_fetched_at)}</span>
        )}
        {feed.consecutive_failures > 0 && (
          <span className="text-yellow-400">
            Failures: {feed.consecutive_failures}
          </span>
        )}
        <button onClick={onToggleTranslate}
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
        <button onClick={onDelete}
          className="px-2 py-1 rounded text-xs bg-bg-card text-red-400 hover:bg-red-900 hover:text-red-200 transition-colors shrink-0">
          Delete
        </button>
      </div>
      {feed.last_error && (
        <div className="text-xs text-red-400 mt-1 truncate" title={feed.last_error}>
          Error: {feed.last_error}
        </div>
      )}
      {feed.extraction_rules != null && onRulesUpdated && (
        <ExtractionRulesEditor feed={feed} onSaved={onRulesUpdated} />
      )}
    </div>
  )
}

function formatRelativeTime(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime()
  const diffMins = Math.floor(diffMs / 60000)
  if (diffMins < 1) return "just now"
  if (diffMins < 60) return `${diffMins}m ago`
  const diffHours = Math.floor(diffMins / 60)
  if (diffHours < 24) return `${diffHours}h ago`
  const diffDays = Math.floor(diffHours / 24)
  return `${diffDays}d ago`
}

export default function Feeds() {
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const [editingFeedId, setEditingFeedId] = useState<number | null>(null)
  const [editFeedName, setEditFeedName] = useState("")
  const [expandedFeeds, setExpandedFeeds] = useState<Set<number>>(new Set())
  const [fetchingFeeds, setFetchingFeeds] = useState<Set<number>>(new Set())
  const { data: feeds, isLoading, isError } = useQuery({
    queryKey: ["feeds"],
    queryFn: api.getFeeds,
  })
  const { data: folders } = useQuery({
    queryKey: ["folders"],
    queryFn: api.getFolders,
  })
  const { data: feedStats } = useQuery({
    queryKey: ["feed-stats"],
    queryFn: api.getFeedStats,
  })

  const sortedFolders = (folders ?? []).slice().sort((a, b) => a.position - b.position || a.id - b.id)
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["feeds"] })
    queryClient.invalidateQueries({ queryKey: ["folders"] })
    queryClient.invalidateQueries({ queryKey: ["feed-stats"] })
  }
  const onError = (e: Error) => setError(e.message)

  const addFeed = useMutation({
    mutationFn: api.createFeed,
    onSuccess: () => { invalidate(); setError(null) },
    onError,
  })
  const toggleFeed = useMutation({ mutationFn: api.toggleFeed, onSuccess: invalidate, onError })
  const toggleSummarize = useMutation({ mutationFn: api.toggleSummarize, onSuccess: invalidate, onError })
  const deleteFeed = useMutation({ mutationFn: api.deleteFeed, onSuccess: invalidate, onError })
  const fetchFeed = useMutation({
    mutationFn: api.fetchFeed,
    onMutate: (feedId: number) => {
      setFetchingFeeds((prev) => new Set(prev).add(feedId))
    },
    onSettled: (_data, _error, feedId) => {
      setTimeout(() => {
        setFetchingFeeds((prev) => {
          const next = new Set(prev)
          next.delete(feedId)
          return next
        })
        invalidate()
      }, 5000)
    },
    onError,
  })
  const renameFeed = useMutation({
    mutationFn: ({ feedId, name }: { feedId: number, name: string }) =>
      api.renameFeed(feedId, name),
    onSuccess: invalidate,
    onError,
  })
  const updateTranslate = useMutation({
    mutationFn: ({ feedId, translate }: { feedId: number, translate: boolean }) =>
      api.updateFeedTranslate(feedId, translate),
    onSuccess: invalidate,
    onError,
  })
  const moveFeed = useMutation({
    mutationFn: ({ feedId, folderId }: { feedId: number, folderId: number | null }) =>
      api.moveFeedToFolder(feedId, folderId),
    onSuccess: invalidate,
    onError,
  })

  // Group feeds by folder
  const feedsByFolder = new Map<number | null, Feed[]>()
  for (const feed of feeds ?? []) {
    const key = feed.folder_id
    if (!feedsByFolder.has(key)) feedsByFolder.set(key, [])
    feedsByFolder.get(key)!.push(feed)
  }

  const toggleExpand = (feedId: number) => {
    setExpandedFeeds((prev) => {
      const next = new Set(prev)
      if (next.has(feedId)) next.delete(feedId)
      else next.add(feedId)
      return next
    })
  }

  const renderFeed = (feed: Feed) => {
    const isExpanded = expandedFeeds.has(feed.id)
    const isUnhealthy = feed.consecutive_failures >= 3

    return (
      <div key={feed.id} className="bg-bg-secondary rounded-lg border border-border">
        <div className="flex items-center justify-between p-3">
          <div className="flex-1 min-w-0 flex items-start gap-2.5">
            {faviconUrl(feed) && (
              <img src={faviconUrl(feed)!} alt="" className="w-4 h-4 shrink-0 mt-1" loading="lazy" />
            )}
            <div className="flex-1 min-w-0">
              {editingFeedId === feed.id ? (
                <input
                  value={editFeedName}
                  onChange={(e) => setEditFeedName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && editFeedName.trim()) {
                      renameFeed.mutate({ feedId: feed.id, name: editFeedName.trim() })
                      setEditingFeedId(null)
                    }
                    if (e.key === "Escape") setEditingFeedId(null)
                  }}
                  onBlur={() => {
                    if (editFeedName.trim() && editFeedName.trim() !== feed.name) {
                      renameFeed.mutate({ feedId: feed.id, name: editFeedName.trim() })
                    }
                    setEditingFeedId(null)
                  }}
                  autoFocus
                  className="bg-bg-card text-text border border-border rounded px-2 py-0.5 text-sm font-medium w-full"
                />
              ) : (
                <div
                  className="flex items-center gap-1.5 cursor-pointer"
                  onClick={() => toggleExpand(feed.id)}
                >
                  <span className="text-text-dim text-xs shrink-0">{isExpanded ? "\u25BE" : "\u25B8"}</span>
                  {feed.extraction_rules != null && (() => {
                    const hasRules = feed.extraction_rules !== "{}"
                    return (
                      <span
                        className={`text-xs px-1 rounded shrink-0 ${
                          hasRules
                            ? "text-accent-text bg-accent-bg"
                            : "text-yellow-400 bg-yellow-400/10 border border-yellow-400/30 border-solid"
                        }`}
                        title={hasRules ? "Web page feed" : "Web page feed — rules not configured"}
                      >
                        WEB{hasRules ? "" : " ⚠"}
                      </span>
                    )
                  })()}
                  {isUnhealthy && (
                    <span
                      className="text-yellow-400 text-sm shrink-0"
                      title={`${feed.consecutive_failures} consecutive failures${feed.last_error ? ": " + feed.last_error : ""}`}
                    >
                      !!!
                    </span>
                  )}
                  <span className={`font-medium ${feed.enabled ? "text-text-heading" : "text-text-dim line-through"}`}>
                    {feed.name}
                  </span>
                  <button
                    onClick={(e) => { e.stopPropagation(); setEditingFeedId(feed.id); setEditFeedName(feed.name) }}
                    className="text-text-dim hover:text-text transition-colors text-xs"
                    title="Rename"
                  >
                    ✎
                  </button>
                </div>
              )}
              <div className="text-xs text-text-muted mt-0.5">
                <a href={feed.site_url ?? feed.url ?? ""} target="_blank" rel="noopener noreferrer" className="text-link hover:text-link-hover hover:underline">{feed.site_url ?? feed.url}</a>
                {feed.site_url && feed.url && (
                  <a href={feed.url} target="_blank" rel="noopener noreferrer" className="text-text-dim hover:text-link-hover ml-1" title={feed.url}>&#8853;</a>
                )}
                {feed.last_fetched_at && (
                  <>{" "}&middot;{" "}{formatRelativeTime(feed.last_fetched_at)}</>
                )}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2 ml-4 shrink-0">
            <button onClick={() => toggleSummarize.mutate(feed.id)}
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
              onChange={(e) => moveFeed.mutate({
                feedId: feed.id,
                folderId: e.target.value ? Number(e.target.value) : null,
              })}
              className="bg-bg-card text-text-muted border border-border rounded px-1.5 py-1 text-xs"
            >
              <option value="">--</option>
              {sortedFolders.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
            </select>
            <button onClick={() => fetchFeed.mutate(feed.id)}
              disabled={fetchingFeeds.has(feed.id)}
              title="Fetch now"
              className="px-2 py-1 rounded text-xs bg-bg-card text-text-muted hover:text-text transition-colors disabled:opacity-40">
              <span className={`inline-block ${fetchingFeeds.has(feed.id) ? "animate-spin" : ""}`}>↻</span>
            </button>
            <button onClick={() => toggleFeed.mutate(feed.id)}
              className={`px-2 py-1 rounded text-xs transition-colors ${
                feed.enabled
                  ? "bg-green-900 text-green-300"
                  : "bg-bg-card text-text-dim"
              }`}>
              {feed.enabled ? "ON" : "OFF"}
            </button>
          </div>
        </div>

        {/* Expandable details */}
        {isExpanded && (
          <FeedDetails feed={feed} stats={feedStats?.[feed.id]}
            onDelete={() => {
              if (confirm(`Delete "${feed.name}"?`)) deleteFeed.mutate(feed.id)
            }}
            onToggleTranslate={() => updateTranslate.mutate({ feedId: feed.id, translate: !feed.translate })}
            onRulesUpdated={feed.extraction_rules != null ? invalidate : undefined}
          />
        )}
      </div>
    )
  }

  const uncategorized = feedsByFolder.get(null) ?? []

  return (
    <div className="flex flex-col h-screen">
      <Header />
      <main className="flex-1 overflow-y-auto px-7 py-5 max-w-6xl">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-text-heading">Feeds</h2>
          <div className="flex items-center gap-2">
            <OpmlButtons onError={onError} onImported={invalidate} feedCount={feeds?.length ?? 0} />
            <button
              onClick={() => {
                if (confirm("Delete ALL feeds, folders, and articles? This cannot be undone.")) {
                  api.deleteAllFeeds().then(invalidate).catch(onError)
                }
              }}
              disabled={!feeds || feeds.length === 0}
              className="px-3 py-1.5 rounded text-sm bg-bg-card text-red-400 border border-border hover:bg-red-900 hover:text-red-200 transition-colors disabled:opacity-40 disabled:pointer-events-none"
            >
              Delete All
            </button>
          </div>
        </div>

        <FolderManager folders={sortedFolders} onError={onError} />

        <AddFeedForm onAdd={(f) => addFeed.mutate(f)} isAdding={addFeed.isPending} folders={sortedFolders} />

        {error && (
          <div className="text-red-400 text-sm mb-4">{error}</div>
        )}

        {isError ? (
          <div className="text-red-400">Failed to load feeds.</div>
        ) : isLoading ? (
          <div className="text-text-muted">Loading...</div>
        ) : (
          <div className="space-y-4">
            {sortedFolders.map((folder) => {
              const folderFeeds = feedsByFolder.get(folder.id) ?? []
              if (folderFeeds.length === 0) return null
              return (
                <div key={folder.id}>
                  <h3 className="text-sm font-medium text-text-muted mb-2">{folder.name}</h3>
                  <div className="space-y-2 ml-2">
                    {folderFeeds.map(renderFeed)}
                  </div>
                </div>
              )
            })}
            {uncategorized.length > 0 && (
              <div>
                {sortedFolders.length > 0 && (
                  <h3 className="text-sm font-medium text-text-muted mb-2">Uncategorized</h3>
                )}
                <div className={`space-y-2 ${sortedFolders.length > 0 ? "ml-2" : ""}`}>
                  {uncategorized.map(renderFeed)}
                </div>
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  )
}
