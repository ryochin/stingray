/**
 * Guards the duplicate warning on the Filters screen: an equivalent pattern is
 * caught while the user types, so the Add button never fires a request the
 * backend would answer with 409.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import type { JSX } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import Filters from "./Filters"

// The route chrome is irrelevant here and would drag in refresh polling.
vi.mock("../components/Header", () => ({
  default: (): JSX.Element | null => null,
}))

const createFilter = vi.fn()

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>()
  return {
    ...actual,
    api: {
      getFilters: vi
        .fn()
        .mockResolvedValue([{ id: 1, pattern: "spam", target: "title" }]),
      createFilter: (...args: unknown[]): unknown => createFilter(...args),
      deleteFilter: vi.fn(),
    },
  }
})

function renderFilters(): void {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <Filters />
    </QueryClientProvider>,
  )
}

function getPatternInput(): HTMLInputElement {
  return screen.getByPlaceholderText("keyword or /regex/") as HTMLInputElement
}

function getAddButton(): HTMLButtonElement {
  return screen.getByRole("button", { name: "Add" }) as HTMLButtonElement
}

beforeEach((): void => {
  createFilter.mockReset()
})

afterEach((): void => {
  document.body.innerHTML = ""
})

describe("Filters duplicate detection", (): void => {
  it("warns when the pattern already exists", async (): Promise<void> => {
    renderFilters()
    await screen.findByText("spam")

    fireEvent.change(getPatternInput(), { target: { value: " SPAM " } })

    await screen.findByText(/This filter already exists/)
    // The cached list may be stale, so the add itself stays available.
    expect(getAddButton().disabled).toBe(false)
  })

  it("shows the conflict the backend reports", async (): Promise<void> => {
    createFilter.mockRejectedValue(new Error('Filter "spam" already exists'))
    renderFilters()
    await screen.findByText("spam")

    fireEvent.change(getPatternInput(), { target: { value: "spam" } })
    fireEvent.submit(getPatternInput().closest("form") as HTMLFormElement)

    await screen.findByText('Filter "spam" already exists')
    expect(getPatternInput().value).toBe("spam")
  })

  it("allows the same pattern on the wider scope", async (): Promise<void> => {
    createFilter.mockResolvedValue({ id: 2, pattern: "spam", target: "both" })
    renderFilters()
    await screen.findByText("spam")

    fireEvent.change(getPatternInput(), { target: { value: "spam" } })
    fireEvent.change(screen.getByRole("combobox"), {
      target: { value: "both" },
    })

    expect(screen.queryByText(/This filter already exists/)).toBeNull()
    fireEvent.submit(getPatternInput().closest("form") as HTMLFormElement)

    await waitFor((): void =>
      expect(createFilter).toHaveBeenCalledWith("spam", "both"),
    )
  })

  it("allows the same pattern on the URL scope", async (): Promise<void> => {
    createFilter.mockResolvedValue({ id: 2, pattern: "spam", target: "url" })
    renderFilters()
    await screen.findByText("spam")

    fireEvent.change(getPatternInput(), { target: { value: "spam" } })
    fireEvent.change(screen.getByRole("combobox"), {
      target: { value: "url" },
    })

    expect(screen.queryByText(/This filter already exists/)).toBeNull()
    fireEvent.submit(getPatternInput().closest("form") as HTMLFormElement)

    await waitFor((): void =>
      expect(createFilter).toHaveBeenCalledWith("spam", "url"),
    )
  })

  it("allows a pattern that is not a duplicate", async (): Promise<void> => {
    createFilter.mockResolvedValue({ id: 2, pattern: "ham", target: "title" })
    renderFilters()
    await screen.findByText("spam")

    fireEvent.change(getPatternInput(), { target: { value: "ham" } })
    expect(getAddButton().disabled).toBe(false)

    fireEvent.submit(getPatternInput().closest("form") as HTMLFormElement)
    await waitFor((): void =>
      expect(createFilter).toHaveBeenCalledWith("ham", "title"),
    )
  })
})
