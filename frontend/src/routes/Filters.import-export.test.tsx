/**
 * Filters export, import and delete: failures surface as an inline error, a
 * successful import reports the counts and refreshes everything that depends
 * on filters (including the sidebar's feed-stats), and the file input is
 * cleared once the upload settles so the same file can be picked again.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { JSX } from "react"
import type { Mock } from "vitest"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { FilterRule } from "../api/client"
import { api } from "../api/client"
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
      getFilters: vi.fn(),
      createFilter: vi.fn(),
      deleteFilter: vi.fn(),
      exportFilters: vi.fn(),
      importFilters: vi.fn(),
    },
  }
})

type ImportResult = Awaited<ReturnType<typeof api.importFilters>>

const SPAM: FilterRule = { id: 7, pattern: "spam", target: "title" }

function renderFilters(): QueryClient {
  const client: QueryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <Filters />
    </QueryClientProvider>,
  )
  return client
}

function fileInput(): HTMLInputElement {
  const input: Element | null = document.querySelector('input[type="file"]')
  if (!(input instanceof HTMLInputElement)) throw new Error("no file input")
  return input
}

function exportButton(): HTMLButtonElement {
  return screen.getByRole("button", {
    name: "Export JSON",
  }) as HTMLButtonElement
}

function invalidatedKeys(spy: Mock): unknown[] {
  return spy.mock.calls.map(
    (call: unknown[]): unknown => (call[0] as { queryKey: unknown }).queryKey,
  )
}

let alertMock: Mock

beforeEach((): void => {
  vi.mocked(api.getFilters).mockResolvedValue([SPAM])
  alertMock = vi.fn()
  vi.stubGlobal("alert", alertMock)
})

afterEach((): void => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.mocked(api.getFilters).mockReset()
  vi.mocked(api.deleteFilter).mockReset()
  vi.mocked(api.exportFilters).mockReset()
  vi.mocked(api.importFilters).mockReset()
})

describe("Filters export", (): void => {
  it("is disabled while there are no filters", async (): Promise<void> => {
    vi.mocked(api.getFilters).mockResolvedValue([])
    renderFilters()

    await waitFor((): void => expect(api.getFilters).toHaveBeenCalled())
    await act(async (): Promise<void> => {})

    expect(exportButton()).toBeDisabled()
  })

  it("downloads once when pressed", async (): Promise<void> => {
    vi.mocked(api.exportFilters).mockResolvedValue(undefined)
    renderFilters()
    await screen.findByText("spam")
    expect(exportButton()).toBeEnabled()

    await act(async (): Promise<void> => {
      fireEvent.click(exportButton())
    })

    expect(api.exportFilters).toHaveBeenCalledTimes(1)
  })

  it.each([
    {
      label: "an Error",
      reason: new Error("Export failed: 500 Internal Server Error"),
      shown: "Export failed: 500 Internal Server Error",
    },
    { label: "a non-Error", reason: "boom", shown: "Export failed" },
  ])("shows the failure for $label", async ({
    reason,
    shown,
  }: {
    reason: unknown
    shown: string
  }): Promise<void> => {
    vi.mocked(api.exportFilters).mockRejectedValue(reason)
    renderFilters()
    await screen.findByText("spam")

    fireEvent.click(exportButton())

    expect(await screen.findByText(shown)).toBeInTheDocument()
  })
})

describe("Filters import", (): void => {
  const file: File = new File(['[{"pattern":"x"}]'], "filters.json", {
    type: "application/json",
  })

  it("uploads, reports the counts, refreshes and clears the input", async (): Promise<void> => {
    let resolve: (value: ImportResult) => void = (): void => {}
    vi.mocked(api.importFilters).mockReturnValue(
      new Promise<ImportResult>((r): void => {
        resolve = r
      }),
    )
    const client: QueryClient = renderFilters()
    await screen.findByText("spam")
    const invalidate = vi.spyOn(client, "invalidateQueries")

    await userEvent.upload(fileInput(), file)

    expect(api.importFilters).toHaveBeenCalledExactlyOnceWith(file)
    // Still pending: the input holds the selection until the upload settles.
    expect(fileInput().value).not.toBe("")

    await act(async (): Promise<void> => {
      resolve({ created: 3, skipped: 1 })
    })

    expect(alertMock).toHaveBeenCalledExactlyOnceWith(
      "Imported 3 filters (1 skipped)",
    )
    const keys: unknown[] = invalidatedKeys(invalidate)
    expect(keys).toContainEqual(["filters"])
    expect(keys).toContainEqual(["articles"])
    expect(keys).toContainEqual(["feed-stats"])
    expect(fileInput().value).toBe("")
  })

  it("shows a failure, clears the input, and a later success clears the error", async (): Promise<void> => {
    let reject: (reason: Error) => void = (): void => {}
    vi.mocked(api.importFilters).mockReturnValueOnce(
      new Promise<ImportResult>((_r, j): void => {
        reject = j
      }),
    )
    renderFilters()
    await screen.findByText("spam")

    await userEvent.upload(fileInput(), file)
    expect(fileInput().value).not.toBe("")
    await act(async (): Promise<void> => {
      reject(new Error("bad json"))
    })

    expect(screen.getByText("bad json")).toBeInTheDocument()
    expect(alertMock).not.toHaveBeenCalled()
    expect(fileInput().value).toBe("")

    vi.mocked(api.importFilters).mockResolvedValueOnce({
      created: 1,
      skipped: 0,
    })
    await act(async (): Promise<void> => {
      await userEvent.upload(fileInput(), file)
    })

    expect(alertMock).toHaveBeenCalledExactlyOnceWith(
      "Imported 1 filters (0 skipped)",
    )
    expect(screen.queryByText("bad json")).toBeNull()
  })

  it("does nothing when the selection is empty", async (): Promise<void> => {
    renderFilters()
    await screen.findByText("spam")

    await act(async (): Promise<void> => {
      fireEvent.change(fileInput(), { target: { files: [] } })
    })

    expect(api.importFilters).not.toHaveBeenCalled()
    expect(alertMock).not.toHaveBeenCalled()
  })
})

describe("Filters delete", (): void => {
  it("deletes by id and refreshes the sidebar counts", async (): Promise<void> => {
    vi.mocked(api.deleteFilter).mockResolvedValue(undefined)
    const client: QueryClient = renderFilters()
    await screen.findByText("spam")
    const invalidate = vi.spyOn(client, "invalidateQueries")

    fireEvent.click(screen.getByRole("button", { name: "Delete" }))

    await waitFor((): void =>
      expect(invalidatedKeys(invalidate)).toContainEqual(["feed-stats"]),
    )
    // mutationFn is api.deleteFilter itself, so TanStack appends its context.
    expect(vi.mocked(api.deleteFilter).mock.calls[0][0]).toBe(SPAM.id)
  })

  it("shows the failure", async (): Promise<void> => {
    vi.mocked(api.deleteFilter).mockRejectedValue(new Error("gone"))
    renderFilters()
    await screen.findByText("spam")

    fireEvent.click(screen.getByRole("button", { name: "Delete" }))

    expect(await screen.findByText("gone")).toBeInTheDocument()
  })
})
