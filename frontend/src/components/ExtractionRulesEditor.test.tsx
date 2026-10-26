/**
 * Covers the LLM selector-inference UX in the rules editor. api.inferRules /
 * api.updateFeedRules are spied so no network is touched.
 */

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
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

const VALID_RULES: Record<string, string> = {
  item: "li.entry",
  title: "a.t",
  link: "a.t",
}

function typeRules(value: string): void {
  // fireEvent rather than userEvent.type, which treats "{" as a key token.
  fireEvent.change(screen.getByRole("textbox"), { target: { value } })
}

function clickSave(): void {
  fireEvent.click(screen.getByRole("button", { name: /Save Rules/i }))
}

describe("ExtractionRulesEditor save", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it.each([
    ["an array", "[1]", "Must be a JSON object"],
    ["a number", "1", "Must be a JSON object"],
    [
      "no item",
      '{"title": "a", "link": "a"}',
      "item, title, link are required",
    ],
    [
      "no title",
      '{"item": "li", "link": "a"}',
      "item, title, link are required",
    ],
    [
      "no link",
      '{"item": "li", "title": "a"}',
      "item, title, link are required",
    ],
    [
      "an empty item",
      '{"item": "", "title": "a", "link": "a"}',
      "item, title, link are required",
    ],
    ["broken JSON", "{", "Invalid JSON"],
    // Current behavior: `null.item` throws inside the try, so the catch
    // reports it as invalid JSON rather than "not an object".
    ["null", "null", "Invalid JSON"],
  ])("rejects %s without saving", async (_label: string, value: string, message: string): Promise<void> => {
    const save = vi.spyOn(api, "updateFeedRules").mockResolvedValue(makeFeed())
    const onSaved = vi.fn()
    render(<ExtractionRulesEditor feed={makeFeed()} onSaved={onSaved} />)

    typeRules(value)
    await act(async (): Promise<void> => {
      clickSave()
    })

    expect(screen.getByText(message)).toBeInTheDocument()
    expect(save).not.toHaveBeenCalled()
    expect(onSaved).not.toHaveBeenCalled()
  })

  it("saves the parsed rules and reports success", async (): Promise<void> => {
    const save = vi.spyOn(api, "updateFeedRules").mockResolvedValue(makeFeed())
    const onSaved = vi.fn()
    render(<ExtractionRulesEditor feed={makeFeed()} onSaved={onSaved} />)

    typeRules(JSON.stringify(VALID_RULES))
    clickSave()

    await waitFor((): void => expect(onSaved).toHaveBeenCalledTimes(1))
    // An object, not the raw editor text.
    expect(save).toHaveBeenCalledWith(42, VALID_RULES)
    expect(screen.getByText("✓ Saved")).toBeInTheDocument()
  })

  it("shows the failure and does not report success", async (): Promise<void> => {
    vi.spyOn(api, "updateFeedRules").mockRejectedValue(new Error("boom"))
    const onSaved = vi.fn()
    render(<ExtractionRulesEditor feed={makeFeed()} onSaved={onSaved} />)

    typeRules(JSON.stringify(VALID_RULES))
    clickSave()

    await screen.findByText("boom")
    expect(onSaved).not.toHaveBeenCalled()
    expect(screen.queryByText("✓ Saved")).toBeNull()
    // `finally` puts the button back from "Saving...".
    expect(
      screen.getByRole("button", { name: /Save Rules/i }),
    ).not.toBeDisabled()
  })
})
