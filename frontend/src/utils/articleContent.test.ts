import { describe, it, expect } from "vitest"
import { parseSummary } from "./articleContent"


describe("parseSummary", () => {
  it("returns plain text and empty image list when no markers", () => {
    expect(parseSummary("ただの要約です")).toEqual({
      text: "ただの要約です",
      imageUrls: [],
    })
  })

  it("extracts a single image URL and strips the marker from text", () => {
    const { text, imageUrls } = parseSummary(
      "前段<image>https://example.com/a.png</image>後段",
    )
    expect(text).toBe("前段後段")
    expect(imageUrls).toEqual(["https://example.com/a.png"])
  })

  it("extracts multiple image URLs preserving order", () => {
    const { text, imageUrls } = parseSummary(
      "<image>https://a/1.png</image> mid <image>https://a/2.png</image>",
    )
    expect(text).toBe("mid")
    expect(imageUrls).toEqual(["https://a/1.png", "https://a/2.png"])
  })

  it("trims whitespace inside image markers", () => {
    const { imageUrls } = parseSummary("<image>   https://a/x.png\n  </image>")
    expect(imageUrls).toEqual(["https://a/x.png"])
  })

  it("trims leading and trailing whitespace in text", () => {
    const { text } = parseSummary("   padded   ")
    expect(text).toBe("padded")
  })

  it("handles image markers spanning newlines", () => {
    const { imageUrls } = parseSummary(
      "before<image>\nhttps://a/x.png\n</image>after",
    )
    expect(imageUrls).toEqual(["https://a/x.png"])
  })

  it("empty input yields empty output", () => {
    expect(parseSummary("")).toEqual({ text: "", imageUrls: [] })
  })
})
