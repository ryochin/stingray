import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  type Mock,
  vi,
} from "vitest"
import { ApiError, api, type Feed, faviconUrl, type Selection } from "./client"

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

// vi.stubGlobal is not undone by restoreAllMocks.
afterEach((): void => {
  vi.unstubAllGlobals()
})

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

  it("keeps an object detail's message and the parsed body", async () => {
    const body: unknown = {
      detail: {
        message: "Not a feed",
        candidates: [{ href: "https://e.com/feed", title: "E", type: "rss" }],
      },
    }
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (): Promise<Response> =>
          Promise.resolve(
            new Response(JSON.stringify(body), {
              status: 422,
              statusText: "Unprocessable Entity",
              headers: { "Content-Type": "application/json" },
            }),
          ),
      ),
    )
    const error: unknown = await api
      .createFeed({ url: "x" })
      .catch((err: unknown): unknown => err)
    expect(error).toBeInstanceOf(ApiError)
    const apiError: ApiError = error as ApiError
    expect(apiError.message).toBe("Not a feed")
    expect(apiError.status).toBe(422)
    // useFeedMutations reads body.detail.candidates from here.
    expect(apiError.body).toEqual(body)
  })

  it("falls back to the status when an object detail has no message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (): Promise<Response> =>
          Promise.resolve(
            new Response(JSON.stringify({ detail: { candidates: [] } }), {
              status: 422,
              // happy-dom does not fill statusText in from the status.
              statusText: "Unprocessable Entity",
              headers: { "Content-Type": "application/json" },
            }),
          ),
      ),
    )
    await expect(api.createFeed({ url: "x" })).rejects.toThrow(
      /^API error: 422 Unprocessable Entity$/,
    )
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

  it.each([
    ["deleteFolder", (): Promise<void> => api.deleteFolder(1)],
    ["reorderFolders", (): Promise<void> => api.reorderFolders([1])],
    ["reorderFeeds", (): Promise<void> => api.reorderFeeds([1])],
    ["deleteFeed", (): Promise<void> => api.deleteFeed(1)],
    ["deleteAllFeeds", (): Promise<void> => api.deleteAllFeeds()],
    ["markRead", (): Promise<void> => api.markRead(["u"])],
    ["markUnread", (): Promise<void> => api.markUnread(["u"])],
    ["deleteFilter", (): Promise<void> => api.deleteFilter(1)],
  ])("%s accepts 204 No Content", async (_name: string, call: () => Promise<void>) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (): Promise<Response> =>
          Promise.resolve(new Response(null, { status: 204 })),
      ),
    )
    expect(await call()).toBeUndefined()
  })

  it("a no-content call still throws ApiError on failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (): Promise<Response> =>
          Promise.resolve(
            new Response(JSON.stringify({ detail: "Folder not found" }), {
              status: 404,
              headers: { "Content-Type": "application/json" },
            }),
          ),
      ),
    )
    const err: unknown = await api.deleteFolder(1).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(404)
    expect((err as ApiError).message).toBe("Folder not found")
  })

  it("a JSON call rejects a 204 instead of resolving to undefined", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (): Promise<Response> =>
          Promise.resolve(new Response(null, { status: 204 })),
      ),
    )
    await expect(api.getFeeds()).rejects.toThrow()
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

  it("getArticles sends the unread-mode parameters", async () => {
    const spy: Mock<(input: string, init: RequestInit) => Promise<Response>> =
      vi.fn(
        (_input: string, _init: RequestInit): Promise<Response> =>
          Promise.resolve(
            new Response("[]", {
              status: 200,
              headers: { "Content-Type": "application/json" },
            }),
          ),
      )
    vi.stubGlobal("fetch", spy)
    await api.getArticles({
      sinceDays: null,
      unread: true,
      readWithinHours: 24,
    })
    await api.getArticles({ sinceDays: 3 })
    const urls: string[] = spy.mock.calls.map(
      (call: [string, RequestInit]): string => call[0],
    )
    expect(urls).toEqual([
      "/api/articles?unread=true&read_within_hours=24",
      "/api/articles?since_days=3",
    ])
  })

  it("markAllRead scopes the request to the current selection", async () => {
    const spy: Mock<(input: string, init: RequestInit) => Promise<Response>> =
      vi.fn(
        (_input: string, _init: RequestInit): Promise<Response> =>
          Promise.resolve(
            new Response(JSON.stringify({ marked: 1 }), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            }),
          ),
      )
    vi.stubGlobal("fetch", spy)
    const cases: readonly [Selection, string][] = [
      [{ type: "all" }, "/api/articles/read-all?"],
      [{ type: "feed", id: 7 }, "/api/articles/read-all?feed_id=7"],
      [{ type: "folder", id: 3 }, "/api/articles/read-all?folder_id=3"],
    ]
    for (const [scope] of cases) {
      await api.markAllRead(scope)
    }
    const urls: string[] = spy.mock.calls.map(
      (call: [string, RequestInit]): string => call[0],
    )
    expect(urls).toEqual(
      cases.map(([, expected]: [Selection, string]) => expected),
    )
  })

  it("markAllRead keeps the age cutoff alongside the scope", async () => {
    const spy: Mock<(input: string, init: RequestInit) => Promise<Response>> =
      vi.fn(
        (_input: string, _init: RequestInit): Promise<Response> =>
          Promise.resolve(
            new Response(JSON.stringify({ marked: 0 }), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            }),
          ),
      )
    vi.stubGlobal("fetch", spy)
    await api.markAllRead({ type: "folder", id: 3 }, 48)
    const call: [string, RequestInit] = spy.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ]
    expect(call[0]).toBe(
      "/api/articles/read-all?folder_id=3&older_than_hours=48",
    )
  })

  it("markAllUnread scopes the request to the current selection", async () => {
    const spy: Mock<(input: string, init: RequestInit) => Promise<Response>> =
      vi.fn(
        (_input: string, _init: RequestInit): Promise<Response> =>
          Promise.resolve(
            new Response(JSON.stringify({ unmarked: 1 }), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            }),
          ),
      )
    vi.stubGlobal("fetch", spy)
    await api.markAllUnread({ type: "folder", id: 3 })
    const call: [string, RequestInit] = spy.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ]
    expect(call[0]).toBe("/api/articles/unread-all?folder_id=3")
    expect(call[1].method).toBe("POST")
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

describe("api.exportFilters", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it("downloads the response as filters.json", async () => {
    const spy: Mock<(input: string) => Promise<Response>> = vi.fn(
      (_input: string): Promise<Response> =>
        Promise.resolve(
          new Response('[{"pattern":"spam"}]', {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        ),
    )
    vi.stubGlobal("fetch", spy)
    const createObjectURL = vi
      .spyOn(URL, "createObjectURL")
      .mockReturnValue("blob:filters")
    const revokeObjectURL = vi
      .spyOn(URL, "revokeObjectURL")
      .mockImplementation((): void => {})
    let clicked: HTMLAnchorElement | null = null
    let revokedBeforeClick: boolean | null = null
    // Without the stub, happy-dom would navigate on click.
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ): void {
      clicked = this
      // Revoking before the click would break the download.
      revokedBeforeClick = revokeObjectURL.mock.calls.length > 0
    })

    await api.exportFilters()

    expect(spy).toHaveBeenCalledExactlyOnceWith("/api/filters/export")
    expect(createObjectURL).toHaveBeenCalledTimes(1)
    const blob: Blob = createObjectURL.mock.calls[0][0] as Blob
    expect(await blob.text()).toBe('[{"pattern":"spam"}]')
    expect(clicked).not.toBeNull()
    const anchor: HTMLAnchorElement = clicked as unknown as HTMLAnchorElement
    expect(anchor.download).toBe("filters.json")
    expect(anchor.href).toBe("blob:filters")
    expect(revokedBeforeClick).toBe(false)
    expect(revokeObjectURL).toHaveBeenCalledExactlyOnceWith("blob:filters")
  })

  it.each([
    {
      label: "a JSON body",
      payload: JSON.stringify({ detail: "db down" }),
      body: { detail: "db down" },
    },
    { label: "a non-JSON body", payload: "<html>oops</html>", body: null },
  ])("rejects a failed export with $label and saves nothing", async ({
    payload,
    body,
  }: {
    payload: string
    body: unknown
  }) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (): Promise<Response> =>
          Promise.resolve(
            new Response(payload, {
              status: 500,
              statusText: "Internal Server Error",
            }),
          ),
      ),
    )
    const createObjectURL = vi
      .spyOn(URL, "createObjectURL")
      .mockReturnValue("blob:filters")
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation((): void => {})

    const error: unknown = await api
      .exportFilters()
      .catch((err: unknown): unknown => err)

    expect(error).toBeInstanceOf(ApiError)
    const apiError: ApiError = error as ApiError
    expect(apiError.message).toBe("Export failed: 500 Internal Server Error")
    expect(apiError.status).toBe(500)
    expect(apiError.body).toEqual(body)
    // The error page must not be saved as filters.json.
    expect(createObjectURL).not.toHaveBeenCalled()
    expect(click).not.toHaveBeenCalled()
  })
})
