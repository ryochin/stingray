import { render } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { useDocumentTitle } from "./useDocumentTitle"

interface HarnessProps {
  title: string
}

function Harness({ title }: HarnessProps): null {
  useDocumentTitle(title)
  return null
}

describe("useDocumentTitle", (): void => {
  const ORIGINAL = "Stingray"

  beforeEach((): void => {
    document.title = ORIGINAL
  })

  afterEach((): void => {
    document.title = ORIGINAL
  })

  it("sets document.title to the given string", (): void => {
    render(<Harness title="Stingray (5)" />)
    expect(document.title).toBe("Stingray (5)")
  })

  it("updates document.title when the value changes", (): void => {
    const { rerender } = render(<Harness title="Stingray (5)" />)
    expect(document.title).toBe("Stingray (5)")
    rerender(<Harness title="Stingray (6)" />)
    expect(document.title).toBe("Stingray (6)")
  })

  it("applies the bare 'Stingray' string when caller passes the zero-count form", (): void => {
    // Mirrors the formatting decision used by Articles.tsx: total === 0
    // should yield "Stingray" with no "(0)" suffix.
    render(<Harness title="Stingray" />)
    expect(document.title).toBe("Stingray")
  })

  it("restores the original document.title on unmount", (): void => {
    const { unmount } = render(<Harness title="Stingray (5)" />)
    expect(document.title).toBe("Stingray (5)")
    unmount()
    expect(document.title).toBe(ORIGINAL)
  })
})
