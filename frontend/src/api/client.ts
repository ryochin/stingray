const BASE = "/api"

export interface Feed {
  id: number
  name: string
  type: string
  url: string | null
  subreddit: string | null
  sort: string | null
  lang: string
  max_items: number
  summarize: boolean
  enabled: boolean
}

export interface Article {
  url: string
  feed_id: number | null
  title: string
  title_ja: string | null
  source: string
  published: string | null
  content_snippet: string | null
  summary: string | null
  lang: string | null
}

export interface RefreshStatus {
  running: boolean
  last_started_at: string | null
  last_finished_at: string | null
  last_status: string | null
  last_new_count: number | null
  last_error: string | null
}

export interface FeedCreate {
  name: string
  type: string
  url?: string
  subreddit?: string
  sort?: string
  lang?: string
  max_items?: number
  summarize?: boolean
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init)
  if (!res.ok) {
    throw new Error(`API error: ${res.status} ${res.statusText}`)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export const api = {
  getArticles: (feedId?: number, limit = 500) => {
    const params = new URLSearchParams()
    if (feedId != null) params.set("feed_id", String(feedId))
    params.set("limit", String(limit))
    return fetchJson<Article[]>(`/articles?${params}`)
  },

  getFeeds: () => fetchJson<Feed[]>("/feeds"),

  createFeed: (body: FeedCreate) =>
    fetchJson<Feed>("/feeds", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  deleteFeed: (id: number) =>
    fetchJson<void>(`/feeds/${id}`, { method: "DELETE" }),

  toggleFeed: (id: number) =>
    fetchJson<Feed>(`/feeds/${id}/toggle`, { method: "POST" }),

  toggleSummarize: (id: number) =>
    fetchJson<Feed>(`/feeds/${id}/summarize`, { method: "POST" }),

  refresh: () =>
    fetchJson<{ message: string }>("/refresh", { method: "POST" }),

  getStatus: () => fetchJson<RefreshStatus>("/status"),
}
