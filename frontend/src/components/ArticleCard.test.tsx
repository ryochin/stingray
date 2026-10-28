import { fireEvent, render, screen } from "@testing-library/react"
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
      <ArticleCard
        article={article({ title_translated: "Translated title" })}
      />,
    )
    expect(screen.getByText("Translated title")).toBeInTheDocument()
    expect(screen.getByText("Original Title")).toBeInTheDocument()
  })

  it("does not duplicate title when translation equals original", () => {
    render(
      <ArticleCard article={article({ title_translated: "Original Title" })} />,
    )
    expect(screen.getAllByText("Original Title")).toHaveLength(1)
  })

  it("keeps the body DOM across re-renders so a broken image does not reload", () => {
    // React 19 rewrites innerHTML whenever the dangerouslySetInnerHTML object
    // changes identity. Rebuilding the body on every re-render made a broken
    // image reload, resize the card, and re-render it again in a loop.
    const a: Article = article({
      content_html: '<p>Body</p><img src="https://img.example.com/broken.svg">',
    })
    const { container, rerender } = render(
      <ArticleCard article={a} focused={false} />,
    )
    const before: Element | null = container.querySelector(".article-html img")
    expect(before).not.toBeNull()
    rerender(<ArticleCard article={{ ...a }} focused />)
    expect(container.querySelector(".article-html img")).toBe(before)
  })

  it("renders summary text extracted from <image> markers", () => {
    const a = article({
      summary: "Summary body<image>https://img.example.com/x.png</image>",
    })
    const { container } = render(<ArticleCard article={a} />)
    expect(screen.getByText("Summary body")).toBeInTheDocument()
    const img = container.querySelector(
      "img[src='https://img.example.com/x.png']",
    )
    expect(img).not.toBeNull()
  })

  it("hides summary image list when sanitizedHtml is present (avoid duplicate)", () => {
    const a = article({
      summary: "Summary text<image>https://img.example.com/x.png</image>",
      content_html: "<p>body</p>",
    })
    const { container } = render(<ArticleCard article={a} />)
    // The explicit image markup from summary should be suppressed because
    // content_html renders images already.
    expect(
      container.querySelector("img[src='https://img.example.com/x.png']"),
    ).toBeNull()
  })

  it("shows 'Awaiting summary...' when pendingKind is 'summary'", () => {
    render(<ArticleCard article={article()} pendingKind="summary" />)
    expect(screen.getByText("Awaiting summary...")).toBeInTheDocument()
  })

  it("shows 'Awaiting translation...' when pendingKind is 'translation'", () => {
    render(<ArticleCard article={article()} pendingKind="translation" />)
    expect(screen.getByText("Awaiting translation...")).toBeInTheDocument()
  })

  it("shows no placeholder when pendingKind is absent", () => {
    render(<ArticleCard article={article()} />)
    expect(screen.queryByText("Awaiting summary...")).toBeNull()
    expect(screen.queryByText("Awaiting translation...")).toBeNull()
  })

  it("shows content_snippet only when no summary / html / translation exist", () => {
    render(
      <ArticleCard article={article({ content_snippet: "Short snippet" })} />,
    )
    expect(screen.getByText("Short snippet")).toBeInTheDocument()
  })

  it("suppresses content_snippet when sanitized HTML is available (deduplication)", () => {
    // Regression: plain-text RSS description sometimes equals content_html, so
    // we guard against double display.
    render(
      <ArticleCard
        article={article({
          content_snippet: "Same body text",
          content_html: "<p>Same body text</p>",
        })}
      />,
    )
    expect(screen.queryByText("Same body text")).toBeInTheDocument()
    // Only one occurrence because snippet path is suppressed.
    expect(screen.getAllByText("Same body text")).toHaveLength(1)
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

  it("does not consume Space (lets browser scroll / global shortcut take over)", () => {
    const onClick: Mock = vi.fn()
    const { container } = render(
      <ArticleCard article={article()} onClick={onClick} />,
    )
    const card = container.firstElementChild as HTMLElement
    // fireEvent.keyDown uses dispatchEvent under the hood and reports
    // whether preventDefault was called via the returned bool.
    const notPrevented = fireEvent.keyDown(card, { key: " " })
    expect(onClick).not.toHaveBeenCalled()
    // fireEvent returns true when the event was NOT canceled (i.e. no
    // preventDefault), which is what we want for Space to scroll.
    expect(notPrevented).toBe(true)
  })

  it("still fires onClick on Enter (and prevents default)", () => {
    const onClick: Mock = vi.fn()
    const { container } = render(
      <ArticleCard article={article()} onClick={onClick} />,
    )
    const card = container.firstElementChild as HTMLElement
    const notPrevented = fireEvent.keyDown(card, { key: "Enter" })
    expect(onClick).toHaveBeenCalledTimes(1)
    expect(notPrevented).toBe(false)
  })

  it("opens body links in a new tab (DOMPurify drops the feed's own target)", () => {
    const a = article({
      content_html:
        '<a href="https://machi.to/bbs/link.cgi" rel="nofollow">link</a>',
    })
    const { container } = render(<ArticleCard article={a} />)
    const link = container.querySelector(
      "a[href='https://machi.to/bbs/link.cgi']",
    ) as HTMLAnchorElement
    expect(link).toHaveAttribute("target", "_blank")
    expect(link.relList.contains("noopener")).toBe(true)
    expect(link.relList.contains("noreferrer")).toBe(true)
    // The feed's own rel tokens survive.
    expect(link.relList.contains("nofollow")).toBe(true)
  })

  it("leaves in-page fragment links alone", () => {
    const a = article({ content_html: '<a href="#fn1">fn</a>' })
    const { container } = render(<ArticleCard article={a} />)
    const link = container.querySelector("a[href='#fn1']") as HTMLAnchorElement
    expect(link).not.toBeNull()
    expect(link).not.toHaveAttribute("target")
  })

  it("does not fire the card onClick when a body link is clicked", () => {
    const onClick: Mock = vi.fn()
    const a = article({
      content_html: '<a href="https://example.com/x">link</a>',
    })
    const { container } = render(<ArticleCard article={a} onClick={onClick} />)
    const link = container.querySelector(
      "a[href='https://example.com/x']",
    ) as HTMLAnchorElement
    const notPrevented = fireEvent.click(link, { cancelable: true })
    expect(onClick).not.toHaveBeenCalled()
    // Stopping propagation must not also suppress the navigation.
    expect(notPrevented).toBe(true)
  })

  it("does not let Enter on a body link reach the card or the global shortcut", () => {
    const onClick: Mock = vi.fn()
    const onWindowKeyDown: Mock = vi.fn()
    window.addEventListener("keydown", onWindowKeyDown)
    try {
      const a = article({
        content_html: '<a href="https://example.com/x">link</a>',
      })
      const { container } = render(
        <ArticleCard article={a} onClick={onClick} />,
      )
      const link = container.querySelector(
        "a[href='https://example.com/x']",
      ) as HTMLAnchorElement
      const notPrevented = fireEvent.keyDown(link, {
        key: "Enter",
        bubbles: true,
        cancelable: true,
      })
      expect(onClick).not.toHaveBeenCalled()
      // useArticleKeyboard listens on window and would open article.url.
      expect(onWindowKeyDown).not.toHaveBeenCalled()
      // Stopping propagation must not also suppress the link's own action.
      expect(notPrevented).toBe(true)
    } finally {
      window.removeEventListener("keydown", onWindowKeyDown)
    }
  })

  it("lets other shortcut keys on a body link reach the global shortcut", () => {
    const onWindowKeyDown: Mock = vi.fn()
    window.addEventListener("keydown", onWindowKeyDown)
    try {
      const a = article({
        content_html: '<a href="https://example.com/x">link</a>',
      })
      const { container } = render(<ArticleCard article={a} />)
      const link = container.querySelector(
        "a[href='https://example.com/x']",
      ) as HTMLAnchorElement
      for (const key of ["j", "k", "m", "?"]) {
        fireEvent.keyDown(link, { key, bubbles: true })
      }
      expect(onWindowKeyDown).toHaveBeenCalledTimes(4)
    } finally {
      window.removeEventListener("keydown", onWindowKeyDown)
    }
  })

  it("treats image map areas like body links", () => {
    const onClick: Mock = vi.fn()
    const a = article({
      content_html:
        '<map name="m"><area href="https://example.com/d" shape="rect" coords="0,0,1,1"></map>',
    })
    const { container } = render(<ArticleCard article={a} onClick={onClick} />)
    const area = container.querySelector(
      "area[href='https://example.com/d']",
    ) as HTMLAreaElement
    expect(area).toHaveAttribute("target", "_blank")
    expect(area.relList.contains("noopener")).toBe(true)
    fireEvent.click(area)
    expect(onClick).not.toHaveBeenCalled()
  })

  it.each([
    { label: "the plain title", translated: null, linkText: "Original Title" },
    {
      label: "the translated title",
      translated: "Translated title",
      linkText: "Translated title",
    },
    {
      label: "the original under a translation",
      translated: "Translated title",
      linkText: "Original Title",
    },
  ])("treats a middle-click on $label as opening the article", ({
    translated,
    linkText,
  }: {
    translated: string | null
    linkText: string
  }): void => {
    const onTitleClick: Mock = vi.fn()
    const onWindowAuxClick: Mock = vi.fn()
    window.addEventListener("auxclick", onWindowAuxClick)
    try {
      render(
        <ArticleCard
          article={article({ title_translated: translated })}
          onTitleClick={onTitleClick}
        />,
      )
      const link: HTMLAnchorElement = screen
        .getByText(linkText)
        .closest("a") as HTMLAnchorElement
      // RTL has no fireEvent.auxClick, and auxclick never triggers the card's
      // onClick, so the stopped propagation is observed from window.
      const notPrevented: boolean = fireEvent(
        link,
        new MouseEvent("auxclick", {
          bubbles: true,
          cancelable: true,
          button: 1,
        }),
      )
      expect(onTitleClick).toHaveBeenCalledTimes(1)
      expect(onWindowAuxClick).not.toHaveBeenCalled()
      // The background tab still opens.
      expect(notPrevented).toBe(true)
    } finally {
      window.removeEventListener("auxclick", onWindowAuxClick)
    }
  })

  it("ignores a right-click on the title", (): void => {
    const onTitleClick: Mock = vi.fn()
    const onWindowAuxClick: Mock = vi.fn()
    window.addEventListener("auxclick", onWindowAuxClick)
    try {
      render(<ArticleCard article={article()} onTitleClick={onTitleClick} />)
      const link: HTMLAnchorElement = screen
        .getByText("Original Title")
        .closest("a") as HTMLAnchorElement
      fireEvent(
        link,
        new MouseEvent("auxclick", {
          bubbles: true,
          cancelable: true,
          button: 2,
        }),
      )
      expect(onTitleClick).not.toHaveBeenCalled()
      expect(onWindowAuxClick).toHaveBeenCalledTimes(1)
    } finally {
      window.removeEventListener("auxclick", onWindowAuxClick)
    }
  })

  // hspace / vspace are also on the strip list, but DOMPurify removes them
  // first, so a test for them would pass with or without the strip.
  it.each([
    { attr: "width", value: "1290" },
    { attr: "height", value: "800" },
    { attr: "style", value: "width:1290px" },
    { attr: "align", value: "left" },
    { attr: "border", value: "1" },
  ])("strips $attr from body images but keeps src", ({
    attr,
    value,
  }: {
    attr: string
    value: string
  }): void => {
    const a: Article = article({
      content_html: `<p><img src="https://img.example.com/a.png" ${attr}="${value}"></p>`,
    })
    const { container } = render(<ArticleCard article={a} />)
    const img: HTMLImageElement = container.querySelector(
      "img[src='https://img.example.com/a.png']",
    ) as HTMLImageElement
    expect(img).not.toBeNull()
    expect(img).not.toHaveAttribute(attr)
  })

  it("clears only the width styling on every ancestor, even of a lazy image", (): void => {
    const a: Article = article({
      content_html:
        '<div width="1290" style="width:1290px; max-width:1290px; min-width:10px; margin:0 auto"><p><img data-src="lazy.png"></p></div>',
    })
    const { container } = render(<ArticleCard article={a} />)
    const img: HTMLImageElement = container.querySelector(
      "img[data-src='lazy.png']",
    ) as HTMLImageElement
    expect(img).not.toBeNull()
    // Two levels up, past the <p>.
    const wrapper: HTMLElement = img.parentElement?.parentElement as HTMLElement
    expect(wrapper.tagName).toBe("DIV")
    expect(wrapper).not.toHaveAttribute("width")
    expect(wrapper.style.getPropertyValue("width")).toBe("")
    expect(wrapper.style.getPropertyValue("max-width")).toBe("")
    expect(wrapper.style.getPropertyValue("min-width")).toBe("")
    // Unrelated inline styling survives.
    expect(wrapper.style.getPropertyValue("margin")).toContain("auto")
  })

  it("drops repeated images, with their link, and keeps one of each", (): void => {
    const a: Article = article({
      content_html: [
        '<p><img src="https://img.example.com/a.png"></p>',
        '<a href="https://example.com/dup"><img src="https://img.example.com/a.png"></a>',
        '<p><img src="https://img.example.com/a.png"></p>',
        '<p><img src="https://img.example.com/b.png"></p>',
      ].join(""),
    })
    const { container } = render(<ArticleCard article={a} />)
    expect(
      container.querySelectorAll("img[src='https://img.example.com/a.png']"),
    ).toHaveLength(1)
    expect(
      container.querySelectorAll("img[src='https://img.example.com/b.png']"),
    ).toHaveLength(1)
    expect(
      container.querySelector("a[href='https://example.com/dup']"),
    ).toBeNull()
  })
})
