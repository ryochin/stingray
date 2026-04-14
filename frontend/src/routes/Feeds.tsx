import { useState, useRef } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { api } from "../api/client"
import type { Feed, Folder, FeedCreate, NgWord } from "../api/client"
import Header from "../components/Header"

function AddFeedForm({ onAdd, isAdding, folders }: { onAdd: (f: FeedCreate) => void, isAdding: boolean, folders: Folder[] }) {
  const [type, setType] = useState("rss")
  const [name, setName] = useState("")
  const [url, setUrl] = useState("")
  const [subreddit, setSubreddit] = useState("")
  const [lang, setLang] = useState("en")
  const [folderId, setFolderId] = useState<number | undefined>(undefined)

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    const body: FeedCreate = { name, type, lang }
    if (type === "reddit") {
      body.subreddit = subreddit
    } else {
      body.url = url
    }
    if (folderId != null) body.folder_id = folderId
    onAdd(body)
  }

  const reset = () => {
    setName("")
    setUrl("")
    setSubreddit("")
    setFolderId(undefined)
  }

  return (
    <form onSubmit={submit} onReset={reset} className="flex flex-wrap gap-3 items-end p-4 bg-bg-secondary rounded-lg border border-border mb-6">
      <div className="flex flex-col gap-1">
        <label className="text-xs text-text-muted">Type</label>
        <select value={type} onChange={(e) => setType(e.target.value)}
          className="bg-bg-card text-text border border-border rounded px-2 py-1.5 text-sm">
          <option value="rss">RSS</option>
          <option value="reddit">Reddit</option>
        </select>
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-xs text-text-muted">Name</label>
        <input value={name} onChange={(e) => setName(e.target.value)} required
          className="bg-bg-card text-text border border-border rounded px-2 py-1.5 text-sm w-40"
          placeholder="Feed name" />
      </div>
      {type === "rss" ? (
        <div className="flex flex-col gap-1 flex-1 min-w-48">
          <label className="text-xs text-text-muted">URL</label>
          <input value={url} onChange={(e) => setUrl(e.target.value)} required
            className="bg-bg-card text-text border border-border rounded px-2 py-1.5 text-sm"
            placeholder="https://example.com/feed" />
        </div>
      ) : (
        <div className="flex flex-col gap-1">
          <label className="text-xs text-text-muted">Subreddit</label>
          <input value={subreddit} onChange={(e) => setSubreddit(e.target.value)} required
            className="bg-bg-card text-text border border-border rounded px-2 py-1.5 text-sm w-36"
            placeholder="programming" />
        </div>
      )}
      <div className="flex flex-col gap-1">
        <label className="text-xs text-text-muted">Lang</label>
        <select value={lang} onChange={(e) => setLang(e.target.value)}
          className="bg-bg-card text-text border border-border rounded px-2 py-1.5 text-sm">
          <option value="en">en</option>
          <option value="ja">ja</option>
        </select>
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
        Add
      </button>
    </form>
  )
}

function FolderManager({ folders, onError }: { folders: Folder[], onError: (e: Error) => void }) {
  const queryClient = useQueryClient()
  const [newName, setNewName] = useState("")
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editName, setEditName] = useState("")

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
            <div key={folder.id} className="flex items-center gap-2 p-2 bg-bg-secondary rounded border border-border">
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

function NgWordManager({ onError }: { onError: (e: Error) => void }) {
  const queryClient = useQueryClient()
  const [pattern, setPattern] = useState("")
  const [target, setTarget] = useState("title")

  const { data: ngWords } = useQuery({
    queryKey: ["ng-words"],
    queryFn: api.getNgWords,
  })

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["ng-words"] })
    queryClient.invalidateQueries({ queryKey: ["articles"] })
  }

  const createNgWord = useMutation({
    mutationFn: ({ pattern, target }: { pattern: string, target: string }) =>
      api.createNgWord(pattern, target),
    onSuccess: () => { invalidate(); setPattern("") },
    onError,
  })
  const deleteNgWord = useMutation({
    mutationFn: api.deleteNgWord,
    onSuccess: invalidate,
    onError,
  })

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault()
    if (!pattern.trim()) return
    createNgWord.mutate({ pattern: pattern.trim(), target })
  }

  const isRegex = (p: string) => p.length > 2 && p.startsWith("/") && p.endsWith("/")

  return (
    <div className="mb-6">
      <h3 className="text-sm font-semibold text-text-heading mb-3">NG Words</h3>
      <form onSubmit={handleCreate} className="flex gap-2 mb-3">
        <input
          value={pattern}
          onChange={(e) => setPattern(e.target.value)}
          className="bg-bg-card text-text border border-border rounded px-2 py-1.5 text-sm flex-1"
          placeholder="keyword or /regex/"
        />
        <select
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          className="bg-bg-card text-text border border-border rounded px-2 py-1.5 text-sm"
        >
          <option value="title">Title</option>
          <option value="both">Both</option>
        </select>
        <button
          type="submit"
          disabled={createNgWord.isPending || !pattern.trim()}
          className="px-3 py-1.5 rounded bg-accent text-white text-sm hover:opacity-90 transition-opacity disabled:opacity-40"
        >
          Add
        </button>
      </form>
      {(ngWords ?? []).length > 0 && (
        <div className="space-y-1">
          {(ngWords ?? []).map((ng: NgWord) => (
            <div key={ng.id} className="flex items-center gap-2 p-2 bg-bg-secondary rounded border border-border">
              <span className={`flex-1 text-sm ${isRegex(ng.pattern) ? "font-mono text-amber-400" : "text-text"}`}>
                {ng.pattern}
              </span>
              <span className="text-xs text-text-dim px-1.5 py-0.5 bg-bg-card rounded">
                {ng.target}
              </span>
              <button
                onClick={() => deleteNgWord.mutate(ng.id)}
                className="px-2 py-1 rounded text-xs bg-bg-card text-red-400 hover:bg-red-900 hover:text-red-200 transition-colors"
              >
                Delete
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function OpmlButtons({ onError, onImported }: { onError: (e: Error) => void, onImported: () => void }) {
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
        className="px-3 py-1.5 rounded text-sm bg-bg-card text-text-muted border border-border hover:text-text transition-colors"
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

export default function Feeds() {
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const { data: feeds, isLoading, isError } = useQuery({
    queryKey: ["feeds"],
    queryFn: api.getFeeds,
  })
  const { data: folders } = useQuery({
    queryKey: ["folders"],
    queryFn: api.getFolders,
  })

  const sortedFolders = (folders ?? []).slice().sort((a, b) => a.position - b.position || a.id - b.id)
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["feeds"] })
    queryClient.invalidateQueries({ queryKey: ["folders"] })
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

  const renderFeed = (feed: Feed) => (
    <div key={feed.id}
      className="flex items-center justify-between p-3 bg-bg-secondary rounded-lg border border-border">
      <div className="flex-1 min-w-0">
        <div className={`font-medium ${feed.enabled ? "text-text-heading" : "text-text-dim line-through"}`}>
          {feed.name}
        </div>
        <div className="text-xs text-text-muted mt-0.5">
          {feed.type === "reddit" ? `r/${feed.subreddit}` : feed.url}
          {" "}&middot;{" "}{feed.lang}
          {" "}&middot;{" "}max {feed.max_items}
        </div>
      </div>
      <div className="flex items-center gap-2 ml-4 shrink-0">
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
        <button onClick={() => toggleSummarize.mutate(feed.id)}
          className={`px-2 py-1 rounded text-xs transition-colors ${
            feed.summarize
              ? "bg-accent-bg text-accent-text"
              : "bg-bg-card text-text-dim"
          }`}>
          LLM
        </button>
        <button onClick={() => toggleFeed.mutate(feed.id)}
          className={`px-2 py-1 rounded text-xs transition-colors ${
            feed.enabled
              ? "bg-green-900 text-green-300"
              : "bg-bg-card text-text-dim"
          }`}>
          {feed.enabled ? "ON" : "OFF"}
        </button>
        <button onClick={() => {
          if (confirm(`Delete "${feed.name}"?`)) deleteFeed.mutate(feed.id)
        }}
          className="px-2 py-1 rounded text-xs bg-bg-card text-red-400 hover:bg-red-900 hover:text-red-200 transition-colors">
          Delete
        </button>
      </div>
    </div>
  )

  const uncategorized = feedsByFolder.get(null) ?? []

  return (
    <div className="flex flex-col h-screen">
      <Header />
      <main className="flex-1 overflow-y-auto px-7 py-5 max-w-4xl">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-text-heading">Feeds</h2>
          <OpmlButtons onError={onError} onImported={invalidate} />
        </div>

        <FolderManager folders={sortedFolders} onError={onError} />
        <NgWordManager onError={onError} />

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
