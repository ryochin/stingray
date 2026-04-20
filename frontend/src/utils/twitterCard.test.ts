import { describe, it, expect } from "vitest"
import { transformTwitterBlockquotes } from "./twitterCard"


function parse(html: string): Document {
  return new DOMParser().parseFromString(`<body>${html}</body>`, "text/html")
}


const sample = `
<blockquote class="twitter-tweet">
<p dir="ltr" lang="ja">松下がパナソニックに変換される話<br />笑った <a href="https://t.co/abc">pic.twitter.com/abc</a></p>
— 𩸽食うクマ (@Bear_eat_Hokke) <a href="https://twitter.com/Bear_eat_Hokke/status/2044912402618274135?ref_src=twsrc%5Etfw">April 16, 2026</a>
</blockquote>
`


describe("transformTwitterBlockquotes", () => {
  it("replaces a twitter-tweet blockquote with a .tweet-card", () => {
    const doc = parse(sample)
    transformTwitterBlockquotes(doc)
    expect(doc.querySelector("blockquote.twitter-tweet")).toBeNull()
    expect(doc.querySelector(".tweet-card")).not.toBeNull()
  })

  it("extracts author name and handle from the meta text", () => {
    const doc = parse(sample)
    transformTwitterBlockquotes(doc)
    expect(doc.querySelector(".tweet-card-author")?.textContent).toBe("𩸽食うクマ")
    const handle = doc.querySelector(".tweet-card-handle")!
    expect(handle.textContent).toBe("@Bear_eat_Hokke")
    expect(handle.getAttribute("href")).toBe("https://x.com/Bear_eat_Hokke")
  })

  it("uses the status link as the card date link (strips query params)", () => {
    const doc = parse(sample)
    transformTwitterBlockquotes(doc)
    const date = doc.querySelector<HTMLAnchorElement>(".tweet-card-date")!
    expect(date.textContent).toBe("April 16, 2026")
    expect(date.getAttribute("href")).toBe(
      "https://twitter.com/Bear_eat_Hokke/status/2044912402618274135",
    )
    expect(date.getAttribute("target")).toBe("_blank")
    expect(date.getAttribute("rel")).toContain("noopener")
  })

  it("preserves inline links and <br> inside the tweet body", () => {
    const doc = parse(sample)
    transformTwitterBlockquotes(doc)
    const body = doc.querySelector(".tweet-card-body")!
    expect(body.innerHTML).toContain("<br>")
    expect(body.querySelector("a[href='https://t.co/abc']")).not.toBeNull()
  })

  it("also accepts x.com status URLs", () => {
    const doc = parse(`
      <blockquote class="twitter-tweet">
      <p>hi</p>
      — n (@handle) <a href="https://x.com/handle/status/123">Jan 1, 2026</a>
      </blockquote>
    `)
    transformTwitterBlockquotes(doc)
    expect(doc.querySelector(".tweet-card-date")?.getAttribute("href"))
      .toBe("https://x.com/handle/status/123")
  })

  it("leaves non-twitter blockquotes untouched", () => {
    const doc = parse(`<blockquote><p>plain quote</p></blockquote>`)
    transformTwitterBlockquotes(doc)
    expect(doc.querySelector("blockquote")).not.toBeNull()
    expect(doc.querySelector(".tweet-card")).toBeNull()
  })

  it("skips when no <p> element is present", () => {
    const doc = parse(`<blockquote class="twitter-tweet">just text</blockquote>`)
    transformTwitterBlockquotes(doc)
    expect(doc.querySelector("blockquote.twitter-tweet")).not.toBeNull()
    expect(doc.querySelector(".tweet-card")).toBeNull()
  })

  it("skips when no status link is present", () => {
    const doc = parse(
      `<blockquote class="twitter-tweet"><p>body</p>— n (@h) no date</blockquote>`,
    )
    transformTwitterBlockquotes(doc)
    expect(doc.querySelector("blockquote.twitter-tweet")).not.toBeNull()
    expect(doc.querySelector(".tweet-card")).toBeNull()
  })

  it("still builds a card when the meta line cannot be parsed", () => {
    const doc = parse(
      `<blockquote class="twitter-tweet"><p>body</p> <a href="https://twitter.com/x/status/1">d</a></blockquote>`,
    )
    transformTwitterBlockquotes(doc)
    expect(doc.querySelector(".tweet-card")).not.toBeNull()
    expect(doc.querySelector(".tweet-card-author")).toBeNull()
    expect(doc.querySelector(".tweet-card-handle")).toBeNull()
  })

  it("transforms multiple tweet blockquotes in one pass", () => {
    const doc = parse(sample + sample)
    transformTwitterBlockquotes(doc)
    expect(doc.querySelectorAll(".tweet-card")).toHaveLength(2)
    expect(doc.querySelector("blockquote.twitter-tweet")).toBeNull()
  })
})
