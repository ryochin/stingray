/**
 * Guards the load-failure banner on the Filters screen: without it a failed
 * `filters` query renders as an empty rule list, indistinguishable from
 * having no filters.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import type { JSX } from "react"
import { afterEach, describe, expect, it, vi } from "vitest"
import Filters from "./Filters"

// The route chrome is irrelevant here and would drag in refresh polling.
vi.mock("../components/Header", () => ({
  default: (): JSX.Element | null => null,
}))

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>()
  return {
    ...actual,
    api: {
      getFilters: vi.fn().mockRejectedValue(new Error("offline")),
    },
  }
})

afterEach((): void => {
  document.body.innerHTML = ""
})

describe("Filters load failure", (): void => {
  it("says the rule list failed to load", async (): Promise<void> => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <QueryClientProvider client={client}>
        <Filters />
      </QueryClientProvider>,
    )

    const banner: HTMLElement = await screen.findByRole("alert")
    expect(banner.textContent).toBe("Failed to load filters.")
  })
})
