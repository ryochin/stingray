/**
 * Folder reordering by drag and drop. The order is checked on screen rather
 * than in the cache: folders render sorted by `position`, so an optimistic
 * update that reshuffles the array but leaves `position` alone would pass a
 * cache check and still show the old order.
 */

import {
  QueryClient,
  QueryClientProvider,
  useQuery,
} from "@tanstack/react-query"
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import type { JSX } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { Folder } from "../api/client"
import { api } from "../api/client"
import FolderManager from "./FolderManager"

const CACHED: Folder[] = [
  { id: 1, name: "Alpha", position: 0 },
  { id: 2, name: "Beta", position: 1 },
  { id: 3, name: "Gamma", position: 2 },
]

let queryClient: QueryClient
let onError: (e: Error) => void

// Stands in for the Feeds route: reads the cache and sorts it the same way.
// Disabled so invalidation leaves the cache to the code under test.
function Harness(): JSX.Element {
  const { data } = useQuery({
    queryKey: ["folders"],
    queryFn: api.getFolders,
    enabled: false,
  })
  const sorted: Folder[] = (data ?? [])
    .slice()
    .sort(
      (a: Folder, b: Folder): number => a.position - b.position || a.id - b.id,
    )
  return <FolderManager folders={sorted} onError={onError} />
}

function setup(): void {
  queryClient.setQueryData<Folder[]>(["folders"], CACHED)
  render(
    <QueryClientProvider client={queryClient}>
      <Harness />
    </QueryClientProvider>,
  )
}

function rowNames(): string[] {
  return screen
    .getAllByRole("listitem")
    .map(
      (row: HTMLElement): string =>
        row.querySelector(".flex-1")?.textContent ?? "",
    )
}

function handleOf(name: string): HTMLElement {
  const row: HTMLElement = screen.getByText(name).closest("li") as HTMLElement
  return row.querySelector("[draggable]") as HTMLElement
}

function drag(from: string, to: string): void {
  fireEvent.dragStart(handleOf(from), {
    dataTransfer: { setDragImage: vi.fn() },
  })
  const target: HTMLElement = screen.getByText(to).closest("li") as HTMLElement
  fireEvent.dragOver(target)
  fireEvent.drop(target)
}

beforeEach((): void => {
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  onError = vi.fn()
})

afterEach((): void => {
  cleanup()
  vi.restoreAllMocks()
})

describe("FolderManager — reorder", (): void => {
  it("shows the new order before the server answers", async (): Promise<void> => {
    // Never resolves: the new order must already be on screen while in flight.
    const reorder = vi
      .spyOn(api, "reorderFolders")
      .mockReturnValue(new Promise(() => {}))
    setup()

    act((): void => drag("Gamma", "Alpha"))

    await waitFor((): void =>
      expect(rowNames()).toEqual(["Gamma", "Alpha", "Beta"]),
    )
    expect(reorder).toHaveBeenCalledWith([3, 1, 2])
  })

  it("rolls the order back and reports the failure", async (): Promise<void> => {
    const failure: Error = new Error("nope")
    vi.spyOn(api, "reorderFolders").mockRejectedValue(failure)
    setup()

    act((): void => drag("Gamma", "Alpha"))

    await waitFor((): void => expect(onError).toHaveBeenCalledWith(failure))
    expect(rowNames()).toEqual(["Alpha", "Beta", "Gamma"])
  })

  it("locks dragging while a reorder is in flight", async (): Promise<void> => {
    vi.spyOn(api, "reorderFolders").mockReturnValue(new Promise(() => {}))
    setup()
    expect(handleOf("Beta").getAttribute("draggable")).toBe("true")

    act((): void => drag("Gamma", "Alpha"))

    await waitFor((): void =>
      expect(handleOf("Beta").getAttribute("draggable")).toBe("false"),
    )
  })

  it.each([
    ["succeeds", (): Promise<void> => Promise.resolve()],
    ["fails", (): Promise<void> => Promise.reject(new Error("nope"))],
  ])("refetches folders once the reorder %s", async (_label, respond): Promise<void> => {
    vi.spyOn(api, "reorderFolders").mockImplementation(respond)
    setup()
    const invalidate = vi.spyOn(queryClient, "invalidateQueries")

    act((): void => drag("Gamma", "Alpha"))

    await waitFor((): void =>
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ["folders"] }),
    )
  })

  it("does not call the API when a folder is dropped on itself", async (): Promise<void> => {
    const reorder = vi.spyOn(api, "reorderFolders")
    setup()

    // Async act: the API call happens after onMutate awaits, so a sync
    // check right after the drop would pass even without the guard.
    await act(async (): Promise<void> => drag("Beta", "Beta"))

    expect(reorder).not.toHaveBeenCalled()
  })
})
