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
  max_items: number
  summarize: boolean
  enabled: boolean
  folder_id: number | null
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
  max_items?: number
  summarize?: boolean
  folder_id?: number
}

export interface NgWord {
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

  getNgWords: () => fetchJson<NgWord[]>("/ng-words"),

  createNgWord: (pattern: string, target: string = "title") =>
    fetchJson<NgWord>("/ng-words", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pattern, target }),
    }),

  deleteNgWord: (id: number) =>
    fetchJson<void>(`/ng-words/${id}`, { method: "DELETE" }),

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
