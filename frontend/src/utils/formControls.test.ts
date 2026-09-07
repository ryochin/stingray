import { describe, expect, it } from "vitest"
import { unwrapFormControls } from "./formControls"

function parse(html: string): Document {
  return new DOMParser().parseFromString(`<body>${html}</body>`, "text/html")
}

// Verbatim excerpt of the GoogleChrome/modern-web-guidance IME guide as it
// arrived in the feed: the tag name is written in prose and left unescaped, so
// the parser opens a real `<textarea>` that never closes.
const unescapedProse: string =
  "Many chat interfaces submit their message when the user presses Enter in a <textarea>. This works for users typing with direct Latin keyboard input."

describe("unwrapFormControls", () => {
  it("rescues body text swallowed by an unclosed <textarea>", () => {
    const doc: Document = parse(unescapedProse)
    expect(doc.querySelectorAll("textarea")).toHaveLength(1)
    unwrapFormControls(doc)
    expect(doc.querySelectorAll("textarea")).toHaveLength(0)
    expect(doc.body.textContent).toContain("direct Latin keyboard input")
  })

  it("unwraps nested controls that a single DOMPurify pass leaves behind", () => {
    const doc: Document = parse(
      "<form action=/x><label for=a>Name</label><input id=a><button>Send</button></form><select><optgroup label=g><option>alpha</option></optgroup></select>",
    )
    unwrapFormControls(doc)
    expect(
      doc.querySelectorAll("form,label,input,button,select,optgroup,option"),
    ).toHaveLength(0)
    expect(doc.body.textContent).toContain("Name")
    expect(doc.body.textContent).toContain("Send")
  })

  it("keeps inline markup nested inside an unwrapped control", () => {
    const doc: Document = parse("<label>plain <b>bold</b></label>")
    unwrapFormControls(doc)
    expect(doc.querySelector("b")?.textContent).toBe("bold")
  })

  it("leaves ordinary article markup untouched", () => {
    const html: string =
      '<figure><img src="x.png"><figcaption>cap</figcaption></figure><pre><code>code</code></pre><blockquote>q</blockquote>'
    const doc: Document = parse(html)
    const before: string = doc.body.innerHTML
    unwrapFormControls(doc)
    expect(doc.body.innerHTML).toBe(before)
  })
})
