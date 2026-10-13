import { render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"
import { failedLoads } from "../utils/queryErrors"
import QueryErrorBanner from "./QueryErrorBanner"

afterEach((): void => {
  document.body.innerHTML = ""
})

describe("QueryErrorBanner", (): void => {
  it("renders nothing while every query is healthy", (): void => {
    const { container } = render(<QueryErrorBanner failed={[]} />)
    expect(container.innerHTML).toBe("")
  })

  it("announces the failed resources in one line", (): void => {
    render(<QueryErrorBanner failed={["feeds", "feed stats"]} />)
    expect(screen.getByRole("alert").textContent).toBe(
      "Failed to load feeds, feed stats.",
    )
  })
})

describe("failedLoads", (): void => {
  it("keeps only the names whose flag is set, in declaration order", (): void => {
    expect(
      failedLoads({ feeds: true, folders: false, "feed stats": true }),
    ).toEqual(["feeds", "feed stats"])
  })
})
