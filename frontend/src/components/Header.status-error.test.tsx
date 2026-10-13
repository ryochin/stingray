/**
 * Guards the status failure marker. Header is the one place the `status`
 * query is reported, since every route mounts it.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { RefreshStatus } from "../api/client"
import Header from "./Header"

const getStatus = vi.fn()

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>()
  return {
    ...actual,
    api: {
      getStatus: (): unknown => getStatus(),
    },
  }
})

const IDLE_STATUS: RefreshStatus = {
  running: false,
  last_started_at: null,
  last_finished_at: null,
  last_status: null,
  last_new_count: null,
  last_error: null,
  llm_enabled: false,
  llm_available: false,
  llm_error: null,
}

function renderHeader(): QueryClient {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Header />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return client
}

beforeEach((): void => {
  getStatus.mockReset()
})

afterEach((): void => {
  document.body.innerHTML = ""
})

describe("Header status failure", (): void => {
  it("marks the status as unavailable", async (): Promise<void> => {
    getStatus.mockRejectedValue(new Error("offline"))
    renderHeader()

    const marker: HTMLElement = await screen.findByRole("alert")
    expect(marker.textContent).toBe("⚠ Status unavailable")
  })

  it("keeps the cached status beside the marker and clears it on recovery", async (): Promise<void> => {
    getStatus.mockResolvedValue(IDLE_STATUS)
    const client: QueryClient = renderHeader()
    await screen.findByText("– LLM off")

    getStatus.mockRejectedValue(new Error("offline"))
    await client.refetchQueries({ queryKey: ["status"] })
    await screen.findByRole("alert")
    expect(screen.getByText("– LLM off")).toBeTruthy()

    getStatus.mockResolvedValue(IDLE_STATUS)
    await client.refetchQueries({ queryKey: ["status"] })
    await waitFor((): void => expect(screen.queryByRole("alert")).toBeNull())
  })
})
