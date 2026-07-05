/**
 * Covers the LLM selector-inference UX in the rules editor. api.inferRules /
 * api.updateFeedRules are spied so no network is touched.
 */

import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"
import type { Feed, InferResult } from "../api/client"
import { api } from "../api/client"
import ExtractionRulesEditor from "./ExtractionRulesEditor"

function makeFeed(overrides: Partial<Feed> = {}): Feed {
  return {
    id: 42,
    name: "Web",
    url: "https://example.com/",
    site_url: null,
    translate: false,
    summarize: false,
    enabled: true,
    folder_id: null,
    position: 0,
    last_fetched_at: null,
    consecutive_failures: 0,
    last_error: null,
    health: "ok",
    extraction_rules: "{}",
    created_at: "2024-01-01T00:00:00Z",
    ...overrides,
  }
}

const OK_RESULT: InferResult = {
  rules: { item: "li.entry", title: "a.t", link: "a.t", link_attr: "href" },
  sample_articles: [
    { title: "First Post", url: "https://example.com/a/1", published: null },
    { title: "Second Post", url: "https://example.com/a/2", published: null },
  ],
  attempts: 1,
  status: "ok",
}

function editorValue(): string {
  return (screen.getByRole("textbox") as HTMLTextAreaElement).value
}

describe("ExtractionRulesEditor inference", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it("infers on button click, fills the editor, and previews samples", async () => {
    const spy = vi.spyOn(api, "inferRules").mockResolvedValue(OK_RESULT)
    render(<ExtractionRulesEditor feed={makeFeed()} onSaved={vi.fn()} />)

    await userEvent.click(
      screen.getByRole("button", { name: /Infer with LLM/i }),
    )

    await waitFor((): void => expect(spy).toHaveBeenCalledWith(42))
    await waitFor((): void => expect(editorValue()).toContain("li.entry"))
    expect(screen.getByText("First Post")).toBeInTheDocument()
    expect(screen.getByText("Second Post")).toBeInTheDocument()
    expect(screen.getByText(/2 articles extracted/)).toBeInTheDocument()
  })

  it("shows a hint when the model returns invalid selectors", async () => {
    vi.spyOn(api, "inferRules").mockResolvedValue({
      rules: { item: "li", title: "a" },
      sample_articles: [],
      attempts: 2,
      status: "invalid",
    })
    render(<ExtractionRulesEditor feed={makeFeed()} onSaved={vi.fn()} />)

    await userEvent.click(
      screen.getByRole("button", { name: /Infer with LLM/i }),
    )

    await waitFor((): void => {
      expect(screen.getByText(/invalid selectors/i)).toBeInTheDocument()
    })
  })

  it("surfaces inference errors without crashing", async () => {
    vi.spyOn(api, "inferRules").mockRejectedValue(new Error("LLM unavailable"))
    render(<ExtractionRulesEditor feed={makeFeed()} onSaved={vi.fn()} />)

    await userEvent.click(
      screen.getByRole("button", { name: /Infer with LLM/i }),
    )

    await waitFor((): void => {
      expect(screen.getByText("LLM unavailable")).toBeInTheDocument()
    })
  })

  it("does not infer on mount — only when the button is clicked", () => {
    const spy = vi.spyOn(api, "inferRules").mockResolvedValue(OK_RESULT)
    render(<ExtractionRulesEditor feed={makeFeed()} onSaved={vi.fn()} />)
    expect(spy).not.toHaveBeenCalled()
  })
})
