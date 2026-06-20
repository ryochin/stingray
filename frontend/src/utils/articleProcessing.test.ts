import { describe, expect, it } from "vitest"
import type { Article } from "../api/client"
import { pendingProcessing, SHORT_SNIPPET_CHARS } from "./articleProcessing"

function article(overrides: Partial<Article> = {}): Article {
  return {
    url: "https://example.com/a",
    feed_id: 1,
    title: "Title",
    title_translated: null,
    source: "F",
    published: null,
    content_snippet: null,
    summary: null,
    content_html: null,
    content_translated: null,
    read_at: null,
    ...overrides,
  }
}

const SHORT: string = "x".repeat(SHORT_SNIPPET_CHARS - 1)
const LONG: string = "x".repeat(SHORT_SNIPPET_CHARS)

describe("pendingProcessing", () => {
  it("translate short article without content_translated → 'translation'", () => {
    expect(
      pendingProcessing(article({ content_snippet: SHORT }), true, false),
    ).toBe("translation")
  })

  it("translate short article with content_translated → null", () => {
    expect(
      pendingProcessing(
        article({ content_snippet: SHORT, content_translated: "訳文" }),
        true,
        false,
      ),
    ).toBeNull()
  })

  it("translate long article, summarize off → null (title-only)", () => {
    expect(
      pendingProcessing(article({ content_snippet: LONG }), true, false),
    ).toBeNull()
  })

  it("translate long article, summarize on, no summary → 'summary'", () => {
    expect(
      pendingProcessing(article({ content_snippet: LONG }), true, true),
    ).toBe("summary")
  })

  it("translate long article, summarize on, content_translated present but no summary → 'summary' (no false-negative)", () => {
    expect(
      pendingProcessing(
        article({ content_snippet: LONG, content_translated: "訳文" }),
        true,
        true,
      ),
    ).toBe("summary")
  })

  it("translate with empty snippet → null (no body to translate)", () => {
    expect(
      pendingProcessing(article({ content_snippet: "" }), true, true),
    ).toBeNull()
    expect(
      pendingProcessing(article({ content_snippet: null }), true, true),
    ).toBeNull()
  })

  it("summarize-only short article → null (won't be summarized)", () => {
    expect(
      pendingProcessing(article({ content_snippet: SHORT }), false, true),
    ).toBeNull()
  })

  it("summarize-only long article, no summary → 'summary'", () => {
    expect(
      pendingProcessing(article({ content_snippet: LONG }), false, true),
    ).toBe("summary")
  })

  it("summarize-only long article with summary present → null (no stale placeholder)", () => {
    expect(
      pendingProcessing(
        article({ content_snippet: LONG, summary: "要約" }),
        false,
        true,
      ),
    ).toBeNull()
  })

  it("neither translate nor summarize → null", () => {
    expect(
      pendingProcessing(article({ content_snippet: LONG }), false, false),
    ).toBeNull()
  })

  it("snippet length boundary: 299 is short, 300 is long (translate-only)", () => {
    // 299 chars → short → full-body translation pending
    expect(
      pendingProcessing(
        article({ content_snippet: "x".repeat(299) }),
        true,
        false,
      ),
    ).toBe("translation")
    // 300 chars → long → translate-only is title-only, nothing pending
    expect(
      pendingProcessing(
        article({ content_snippet: "x".repeat(300) }),
        true,
        false,
      ),
    ).toBeNull()
  })
})
