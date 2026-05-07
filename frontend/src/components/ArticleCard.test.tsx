import { render, screen } from "@testing-library/react"
import type { UserEvent } from "@testing-library/user-event"
import userEvent from "@testing-library/user-event"
import type { Mock } from "vitest"
import { describe, expect, it, vi } from "vitest"
import type { Article } from "../api/client"
import ArticleCard from "./ArticleCard"

function article(overrides: Partial<Article> = {}): Article {
  return {
    url: "https://example.com/a",
    feed_id: 1,
    title: "Original Title",
    title_translated: null,
    source: "F",
    published: "2024-06-15T00:00:00Z",
    content_snippet: null,
    summary: null,
    content_html: null,
    content_translated: null,
    read_at: null,
    ...overrides,
  }
}

describe("ArticleCard", () => {
  it("shows original title only when no translation", () => {
    render(<ArticleCard article={article()} />)
    expect(screen.getByText("Original Title")).toBeInTheDocument()
  })

  it("shows translated title prominently and original as subtext when translation differs", () => {
    render(
      <ArticleCard article={article({ title_translated: "翻訳タイトル" })} />,
    )
    expect(screen.getByText("翻訳タイトル")).toBeInTheDocument()
    expect(screen.getByText("Original Title")).toBeInTheDocument()
  })

  it("does not duplicate title when translation equals original", () => {
    render(
      <ArticleCard article={article({ title_translated: "Original Title" })} />,
    )
    expect(screen.getAllByText("Original Title")).toHaveLength(1)
  })

  it("renders summary text extracted from <image> markers", () => {
    const a = article({
      summary: "要約本文<image>https://img.example.com/x.png</image>",
    })
    const { container } = render(<ArticleCard article={a} />)
    expect(screen.getByText("要約本文")).toBeInTheDocument()
    const img = container.querySelector(
      "img[src='https://img.example.com/x.png']",
    )
    expect(img).not.toBeNull()
  })

  it("hides summary image list when sanitizedHtml is present (avoid duplicate)", () => {
    const a = article({
      summary: "要約<image>https://img.example.com/x.png</image>",
      content_html: "<p>body</p>",
    })
    const { container } = render(<ArticleCard article={a} />)
    // The explicit image markup from summary should be suppressed because
    // content_html renders images already.
    expect(
      container.querySelector("img[src='https://img.example.com/x.png']"),
    ).toBeNull()
  })

  it("shows 'Awaiting summary...' only when pendingSummary and no existing content", () => {
    render(<ArticleCard article={article()} pendingSummary={true} />)
    expect(screen.getByText("Awaiting summary...")).toBeInTheDocument()
  })

  it("hides 'Awaiting summary...' when summary already exists", () => {
    render(
      <ArticleCard
        article={article({ summary: "既要約" })}
        pendingSummary={true}
      />,
    )
    expect(screen.queryByText("Awaiting summary...")).toBeNull()
  })

  it("shows content_snippet only when no summary / html / translation exist", () => {
    render(<ArticleCard article={article({ content_snippet: "短い抜粋" })} />)
    expect(screen.getByText("短い抜粋")).toBeInTheDocument()
  })

  it("suppresses content_snippet when sanitized HTML is available (deduplication)", () => {
    // Regression: plain-text RSS description sometimes equals content_html, so
    // we guard against double display.
    render(
      <ArticleCard
        article={article({
          content_snippet: "同じ本文",
          content_html: "<p>同じ本文</p>",
        })}
      />,
    )
    expect(screen.queryByText("同じ本文")).toBeInTheDocument()
    // Only one occurrence because snippet path is suppressed.
    expect(screen.getAllByText("同じ本文")).toHaveLength(1)
  })

  it("renders feed name and favicon when provided", () => {
    render(
      <ArticleCard
        article={article()}
        feedName="Tech Feed"
        feedFaviconUrl="https://fav.example.com/icon"
      />,
    )
    expect(screen.getByText("Tech Feed")).toBeInTheDocument()
  })

  it("links to the article url with target=_blank and rel=noopener", () => {
    render(<ArticleCard article={article()} />)
    const link = screen.getByText("Original Title").closest("a")!
    expect(link).toHaveAttribute("href", "https://example.com/a")
    expect(link).toHaveAttribute("target", "_blank")
    expect(link.getAttribute("rel")).toContain("noopener")
  })

  it("fires onClick when the card is clicked", async (): Promise<void> => {
    const user: UserEvent = userEvent.setup()
    const onClick: Mock = vi.fn()
    const { container } = render(
      <ArticleCard article={article()} onClick={onClick} />,
    )
    await user.click(container.firstElementChild as HTMLElement)
    expect(onClick).toHaveBeenCalled()
  })
})
