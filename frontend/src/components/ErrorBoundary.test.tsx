import { render, screen } from "@testing-library/react"
import type { JSX } from "react"
import { afterEach, describe, expect, it, vi } from "vitest"
import ErrorBoundary from "./ErrorBoundary"

function Boom(): JSX.Element {
  throw new Error("boom")
}

afterEach((): void => {
  vi.restoreAllMocks()
})

describe("ErrorBoundary", (): void => {
  it("renders the fallback instead of crashing, leaving siblings alone", (): void => {
    // React logs the caught error itself; keep the test output readable.
    vi.spyOn(console, "error").mockImplementation((): void => {})

    render(
      <div>
        <span>sibling</span>
        <ErrorBoundary fallback={<span>fallback</span>}>
          <Boom />
        </ErrorBoundary>
      </div>,
    )

    expect(screen.getByText("fallback")).toBeTruthy()
    expect(screen.getByText("sibling")).toBeTruthy()
  })

  it("clears the error when its key changes", (): void => {
    // App keys the boundary on the route path: the error state is otherwise
    // permanent, and the tab shortcuts can navigate while the fallback is up.
    vi.spyOn(console, "error").mockImplementation((): void => {})

    const { rerender } = render(
      <ErrorBoundary key="/" fallback={<span>fallback</span>}>
        <Boom />
      </ErrorBoundary>,
    )
    expect(screen.getByText("fallback")).toBeTruthy()

    rerender(
      <ErrorBoundary key="/feeds" fallback={<span>fallback</span>}>
        <span>recovered</span>
      </ErrorBoundary>,
    )

    expect(screen.getByText("recovered")).toBeTruthy()
    expect(screen.queryByText("fallback")).toBeNull()
  })
})
