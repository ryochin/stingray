import { beforeEach, describe, expect, it, type Mock, vi } from "vitest"
import { api, type Feed, faviconUrl } from "./client"

function makeFeed(overrides: Partial<Feed> = {}): Feed {
  return {
    id: 1,
    name: "F",
    url: "https://example.com/rss",
    site_url: null,
    translate: false,
    summarize: true,
    enabled: true,
    folder_id: null,
    position: 0,
    last_fetched_at: null,
    consecutive_failures: 0,
    last_error: null,
    health: "ok",
    extraction_rules: null,
    created_at: "2024-01-01T00:00:00Z",
    ...overrides,
  }
}

describe("faviconUrl", () => {
  it("returns google favicon URL for site_url preference", () => {
    const out: string | null = faviconUrl(
      makeFeed({
        site_url: "https://site.example.com/home",
        url: "https://feed.example.com/rss",
      }),
    )
    expect(out).toContain("domain=site.example.com")
  })

  it("falls back to url when site_url is null", () => {
    const out: string | null = faviconUrl(
      makeFeed({ url: "https://feed.example.com/rss" }),
    )
    expect(out).toContain("domain=feed.example.com")
  })

  it("returns null when both urls are null", () => {
    expect(faviconUrl(makeFeed({ url: null, site_url: null }))).toBeNull()
  })

  it("returns null for an unparseable URL", () => {
    expect(
      faviconUrl(makeFeed({ url: "not a url", site_url: null })),
    ).toBeNull()
  })
})

describe("api fetchJson behavior", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it("getFolders returns parsed JSON on success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (): Promise<Response> =>
          Promise.resolve(
            new Response(JSON.stringify([{ id: 1, name: "A", position: 0 }]), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            }),
          ),
      ),
    )
    const folders: Awaited<ReturnType<typeof api.getFolders>> =
      await api.getFolders()
    expect(folders).toEqual([{ id: 1, name: "A", position: 0 }])
  })

  it("surfaces server-provided detail on error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (): Promise<Response> =>
          Promise.resolve(
            new Response(JSON.stringify({ detail: "folder name taken" }), {
              status: 400,
              headers: { "Content-Type": "application/json" },
            }),
          ),
      ),
    )
    await expect(api.createFolder("dup")).rejects.toThrow(/folder name taken/)
  })

  it("falls back to status text when no JSON detail", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (): Promise<Response> =>
          Promise.resolve(
            new Response("", {
              status: 500,
              statusText: "Internal Server Error",
            }),
          ),
      ),
    )
    await expect(api.getFolders()).rejects.toThrow(/500/)
  })

  it("204 returns undefined without parsing body", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (): Promise<Response> =>
          Promise.resolve(new Response(null, { status: 204 })),
      ),
    )
    expect(await api.deleteFolder(1)).toBeUndefined()
  })

  it("inferRules POSTs to the infer endpoint and returns the preview", async () => {
    const payload = {
      rules: { item: "li.entry", title: "a", link: "a" },
      sample_articles: [
        { title: "First", url: "https://example.com/a/1", published: null },
      ],
      attempts: 1,
      status: "ok",
    }
    const spy: Mock<(input: string, init: RequestInit) => Promise<Response>> =
      vi.fn(
        (_input: string, _init: RequestInit): Promise<Response> =>
          Promise.resolve(
            new Response(JSON.stringify(payload), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            }),
          ),
      )
    vi.stubGlobal("fetch", spy)
    const result: Awaited<ReturnType<typeof api.inferRules>> =
      await api.inferRules(7)
    expect(spy).toHaveBeenCalledOnce()
    const call: [string, RequestInit] = spy.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ]
    expect(call[0]).toBe("/api/feeds/7/rules/infer")
    expect(call[1].method).toBe("POST")
    expect(result.status).toBe("ok")
    expect(result.rules.item).toBe("li.entry")
    expect(result.sample_articles[0].title).toBe("First")
  })

  it("markRead POSTs url array as JSON", async () => {
    const spy: Mock<(input: string, init: RequestInit) => Promise<Response>> =
      vi.fn(
        (_input: string, _init: RequestInit): Promise<Response> =>
          Promise.resolve(new Response(null, { status: 204 })),
      )
    vi.stubGlobal("fetch", spy)
    await api.markRead(["u1", "u2"])
    expect(spy).toHaveBeenCalledOnce()
    const call: [string, RequestInit] = spy.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ]
    const init: RequestInit = call[1]
    expect(init.method).toBe("POST")
    expect(init.body).toBe(JSON.stringify({ urls: ["u1", "u2"] }))
  })
})
