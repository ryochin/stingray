const BASE = "/api"

export interface Folder {
  id: number
  name: string
  position: number
}

export interface Feed {
  id: number
  name: string
  url: string | null
  site_url: string | null
  lang: string
  summarize: boolean
  enabled: boolean
  folder_id: number | null
  last_fetched_at: string | null
  consecutive_failures: number
  last_error: string | null
  created_at: string
}

export interface FeedStats {
  article_count: number
  unread_count: number
  latest_published: string | null
  oldest_published: string | null
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
  content_html: string | null
  lang: string | null
  read_at: string | null
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
  name?: string
  url: string
  lang?: string
  summarize?: boolean
  folder_id?: number
}

export interface FilterRule {
  id: number
  pattern: string
  target: string
}

export function faviconUrl(feed: Feed): string | null {
  const url = feed.site_url ?? feed.url
  if (!url) return null
  try {
    const domain = new URL(url).hostname
    return `https://www.google.com/s2/favicons?sz=16&domain=${domain}`
  } catch {
    return null
  }
}

export type Selection =
  | { type: "all" }
  | { type: "folder", id: number }
  | { type: "feed", id: number }

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

  getFolders: () => fetchJson<Folder[]>("/folders"),

  createFolder: (name: string) =>
    fetchJson<Folder>("/folders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }),

  renameFolder: (id: number, name: string) =>
    fetchJson<Folder>(`/folders/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }),

  deleteFolder: (id: number) =>
    fetchJson<void>(`/folders/${id}`, { method: "DELETE" }),

  reorderFolders: (folderIds: number[]) =>
    fetchJson<void>("/folders/reorder", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder_ids: folderIds }),
    }),

  getFeeds: () => fetchJson<Feed[]>("/feeds"),

  getFeedStats: () => fetchJson<Record<string, FeedStats>>("/feeds/stats"),

  moveFeedToFolder: (feedId: number, folderId: number | null) =>
    fetchJson<Feed>(`/feeds/${feedId}/folder`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder_id: folderId }),
    }),

  createFeed: (body: FeedCreate) =>
    fetchJson<Feed>("/feeds", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  fetchFeed: (id: number) =>
    fetchJson<{ message: string }>(`/feeds/${id}/fetch`, { method: "POST" }),

  deleteFeed: (id: number) =>
    fetchJson<void>(`/feeds/${id}`, { method: "DELETE" }),

  deleteAllFeeds: () =>
    fetchJson<void>("/feeds", { method: "DELETE" }),

  toggleFeed: (id: number) =>
    fetchJson<Feed>(`/feeds/${id}/toggle`, { method: "POST" }),

  toggleSummarize: (id: number) =>
    fetchJson<Feed>(`/feeds/${id}/summarize`, { method: "POST" }),

  renameFeed: (id: number, name: string) =>
    fetchJson<Feed>(`/feeds/${id}/name`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }),

  updateFeedLang: (id: number, lang: string) =>
    fetchJson<Feed>(`/feeds/${id}/lang`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lang }),
    }),

  refresh: () =>
    fetchJson<{ message: string }>("/refresh", { method: "POST" }),

  getStatus: () => fetchJson<RefreshStatus>("/status"),

  markRead: (urls: string[]) =>
    fetchJson<void>("/articles/read", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ urls }),
    }),

  markUnread: (urls: string[]) =>
    fetchJson<void>("/articles/unread", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ urls }),
    }),

  markAllRead: (feedId?: number) => {
    const params = new URLSearchParams()
    if (feedId != null) params.set("feed_id", String(feedId))
    return fetchJson<{ marked: number }>(`/articles/read-all?${params}`, { method: "POST" })
  },

  getFilters: () => fetchJson<FilterRule[]>("/filters"),

  createFilter: (pattern: string, target: string = "title") =>
    fetchJson<FilterRule>("/filters", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pattern, target }),
    }),

  deleteFilter: (id: number) =>
    fetchJson<void>(`/filters/${id}`, { method: "DELETE" }),

  exportFilters: async () => {
    const resp = await fetch(`${BASE}/filters/export`)
    if (!resp.ok) throw new Error("Export failed")
    const blob = await resp.blob()
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement("a")
    anchor.href = url
    anchor.download = "filters.json"
    anchor.click()
    URL.revokeObjectURL(url)
  },

  importFilters: async (file: File) => {
    const form = new FormData()
    form.append("file", file)
    const resp = await fetch(`${BASE}/filters/import`, { method: "POST", body: form })
    if (!resp.ok) throw new Error("Import failed")
    return resp.json() as Promise<{ created: number, skipped: number }>
  },

  exportOpml: async () => {
    const res = await fetch(`${BASE}/opml/export`)
    if (!res.ok) throw new Error(`API error: ${res.status}`)
    return res.blob()
  },

  importOpml: (file: File) => {
    const form = new FormData()
    form.append("file", file)
    return fetchJson<{ folders_created: number, feeds_created: number, feeds_skipped: number }>(
      "/opml/import",
      { method: "POST", body: form },
    )
  },
}
