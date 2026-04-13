import { useState } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { api } from "../api/client"
import type { Feed, FeedCreate } from "../api/client"
import Header from "../components/Header"

function AddFeedForm({ onAdd, isAdding }: { onAdd: (f: FeedCreate) => void, isAdding: boolean }) {
  const [type, setType] = useState("rss")
  const [name, setName] = useState("")
  const [url, setUrl] = useState("")
  const [subreddit, setSubreddit] = useState("")
  const [lang, setLang] = useState("en")

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    const body: FeedCreate = { name, type, lang }
    if (type === "reddit") {
      body.subreddit = subreddit
    } else {
      body.url = url
    }
    onAdd(body)
  }

  const reset = () => {
    setName("")
    setUrl("")
    setSubreddit("")
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
      <button type="submit" disabled={isAdding}
        className="px-4 py-1.5 rounded bg-accent text-white text-sm hover:opacity-90 transition-opacity disabled:opacity-40">
        Add
      </button>
    </form>
  )
}

export default function Feeds() {
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const { data: feeds, isLoading, isError } = useQuery({
    queryKey: ["feeds"],
    queryFn: api.getFeeds,
  })

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["feeds"] })
  const onError = (e: Error) => setError(e.message)

  const addFeed = useMutation({
    mutationFn: api.createFeed,
    onSuccess: () => { invalidate(); setError(null) },
    onError,
  })
  const toggleFeed = useMutation({ mutationFn: api.toggleFeed, onSuccess: invalidate, onError })
  const toggleSummarize = useMutation({ mutationFn: api.toggleSummarize, onSuccess: invalidate, onError })
  const deleteFeed = useMutation({ mutationFn: api.deleteFeed, onSuccess: invalidate, onError })

  return (
    <div className="flex flex-col h-screen">
      <Header />
      <main className="flex-1 overflow-y-auto px-7 py-5 max-w-4xl">
        <h2 className="text-lg font-semibold text-text-heading mb-4">Feeds</h2>
        <AddFeedForm onAdd={(f) => addFeed.mutate(f)} isAdding={addFeed.isPending} />

        {error && (
          <div className="text-red-400 text-sm mb-4">{error}</div>
        )}

        {isError ? (
          <div className="text-red-400">Failed to load feeds.</div>
        ) : isLoading ? (
          <div className="text-text-muted">Loading...</div>
        ) : (
          <div className="space-y-2">
            {feeds?.map((feed: Feed) => (
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
            ))}
          </div>
        )}
      </main>
    </div>
  )
}
