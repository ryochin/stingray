import { render } from "@testing-library/react"
import type { JSX } from "react"
import { act } from "react"
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom"
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { useDocumentTitle } from "./useDocumentTitle"

// Toy route components stand in for Articles / Feeds. The point is to assert
// that on route unmount the hook restores the title, and on remount it
// reapplies — independent of the heavy Articles dependency graph.
function HomeRoute({ title }: { title: string }): JSX.Element {
  useDocumentTitle(title)
  return <div data-testid="home" />
}

function OtherRoute(): JSX.Element {
  return <div data-testid="other" />
}

interface HarnessProps {
  title: string
}

let goTo: (path: string) => void = () => {}

function Navigator(): null {
  const navigate = useNavigate()
  goTo = (path: string): void => {
    navigate(path)
  }
  return null
}

function Harness({ title }: HarnessProps): JSX.Element {
  return (
    <MemoryRouter initialEntries={["/"]}>
      <Navigator />
      <Routes>
        <Route path="/" element={<HomeRoute title={title} />} />
        <Route path="/feeds" element={<OtherRoute />} />
      </Routes>
    </MemoryRouter>
  )
}

describe("useDocumentTitle (route integration)", (): void => {
  const ORIGINAL = "Stingray"

  beforeEach((): void => {
    document.title = ORIGINAL
  })

  afterEach((): void => {
    document.title = ORIGINAL
    goTo = () => {}
  })

  it("applies title on home, restores on navigating away, reapplies on return", (): void => {
    render(<Harness title="Stingray (7)" />)
    expect(document.title).toBe("Stingray (7)")

    act((): void => {
      goTo("/feeds")
    })
    expect(document.title).toBe(ORIGINAL)

    act((): void => {
      goTo("/")
    })
    expect(document.title).toBe("Stingray (7)")
  })
})
