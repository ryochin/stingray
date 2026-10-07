import { describe, expect, it } from "vitest"
import { filterKey, filterTargetLabel, isRegexPattern } from "./filterRules"

describe("isRegexPattern", (): void => {
  it("treats a slash-wrapped pattern as a regex", (): void => {
    expect(isRegexPattern("/ab.c/")).toBe(true)
  })

  it("needs more than two characters to wrap", (): void => {
    expect(isRegexPattern("/")).toBe(false)
    expect(isRegexPattern("//")).toBe(false)
  })

  it("needs both slashes", (): void => {
    expect(isRegexPattern("/foo")).toBe(false)
  })
})

// These cases mirror TestFilterKey in backend/tests/test_filters.py: the two
// implementations must agree on what counts as a duplicate.
describe("filterKey", (): void => {
  it("ignores the case of a keyword", (): void => {
    expect(filterKey("Apple", "title")).toBe(filterKey("apple", "title"))
  })

  it("keeps surrounding space significant", (): void => {
    expect(filterKey(" spam ", "title")).not.toBe(filterKey("spam", "title"))
  })

  it("keeps the case of a regex source", (): void => {
    expect(filterKey("/\\S+/", "title")).not.toBe(filterKey("/\\s+/", "title"))
  })

  it("separates a regex from the same keyword", (): void => {
    expect(filterKey("/abc/", "title")).not.toBe(filterKey("abc", "title"))
  })

  it("treats body and both as one scope", (): void => {
    expect(filterKey("spam", "body")).toBe(filterKey("spam", "both"))
  })

  it("separates title from the wider scope", (): void => {
    expect(filterKey("spam", "title")).not.toBe(filterKey("spam", "both"))
  })

  it("keeps url as its own scope", (): void => {
    expect(filterKey("spam", "url")).not.toBe(filterKey("spam", "title"))
    expect(filterKey("spam", "url")).not.toBe(filterKey("spam", "both"))
  })
})

describe("filterTargetLabel", (): void => {
  it("names each target", (): void => {
    expect(filterTargetLabel("title")).toBe("Title")
    expect(filterTargetLabel("both")).toBe("Title & Body")
    expect(filterTargetLabel("url")).toBe("URL")
  })

  it("reads the legacy body target as title and body", (): void => {
    expect(filterTargetLabel("body")).toBe("Title & Body")
  })
})
