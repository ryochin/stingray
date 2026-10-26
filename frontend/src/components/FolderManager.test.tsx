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
  within,
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
  // stubGlobal (confirm) is not undone by restoreAllMocks.
  vi.unstubAllGlobals()
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

  it("ignores a drag that started outside the folder list", async (): Promise<void> => {
    // A feed card dragged across the Feeds screen passes over folder rows.
    const reorder = vi.spyOn(api, "reorderFolders")
    setup()
    const row: HTMLElement = screen
      .getByText("Beta")
      .closest("li") as HTMLElement

    fireEvent.dragOver(row)
    expect(row.className).not.toContain("border-accent")

    await act(async (): Promise<void> => {
      fireEvent.drop(row)
    })
    expect(reorder).not.toHaveBeenCalled()
  })
})

function rowOf(name: string): HTMLElement {
  return screen.getByText(name).closest("li") as HTMLElement
}

function newNameInput(): HTMLInputElement {
  return screen.getByPlaceholderText("New folder name") as HTMLInputElement
}

/** Opens the inline rename for Beta (id 2) and returns its input. */
function startRenameBeta(): HTMLInputElement {
  fireEvent.click(within(rowOf("Beta")).getByRole("button", { name: "Rename" }))
  return screen.getByDisplayValue("Beta") as HTMLInputElement
}

/** The Harness query is disabled, so a refetch shows up only as the call. */
function expectRefetched(invalidate: ReturnType<typeof vi.spyOn>): void {
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ["folders"] })
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ["feeds"] })
}

describe("FolderManager — create", (): void => {
  it("creates a trimmed folder, clears the input, and refetches", async (): Promise<void> => {
    const create = vi
      .spyOn(api, "createFolder")
      .mockResolvedValue({ id: 9, name: "News", position: 3 })
    setup()
    const invalidate = vi.spyOn(queryClient, "invalidateQueries")
    const input: HTMLInputElement = newNameInput()

    fireEvent.change(input, { target: { value: "  News  " } })
    fireEvent.submit(input.closest("form") as HTMLFormElement)

    await waitFor((): void => expect(input.value).toBe(""))
    expect(create).toHaveBeenCalledWith("News")
    expectRefetched(invalidate)
  })

  it("does not create a whitespace-only folder", async (): Promise<void> => {
    const create = vi.spyOn(api, "createFolder")
    setup()
    const input: HTMLInputElement = newNameInput()

    fireEvent.change(input, { target: { value: "   " } })
    expect(screen.getByRole("button", { name: "Add" })).toBeDisabled()
    // Submit directly so the guard in the handler is checked, not the button.
    await act(async (): Promise<void> => {
      fireEvent.submit(input.closest("form") as HTMLFormElement)
    })

    expect(create).not.toHaveBeenCalled()
  })
})

describe("FolderManager — rename", (): void => {
  it("renames with a trimmed name on Enter and leaves edit mode", async (): Promise<void> => {
    const rename = vi
      .spyOn(api, "renameFolder")
      .mockResolvedValue({ id: 2, name: "B2", position: 1 })
    setup()
    const invalidate = vi.spyOn(queryClient, "invalidateQueries")
    const input: HTMLInputElement = startRenameBeta()

    fireEvent.change(input, { target: { value: " B2 " } })
    fireEvent.keyDown(input, { key: "Enter" })

    await waitFor((): void => expect(input.isConnected).toBe(false))
    expect(rename).toHaveBeenCalledWith(2, "B2")
    expectRefetched(invalidate)
  })

  it("does not rename to an empty name on Enter or Save", async (): Promise<void> => {
    const rename = vi.spyOn(api, "renameFolder")
    setup()
    const input: HTMLInputElement = startRenameBeta()

    fireEvent.change(input, { target: { value: "   " } })
    await act(async (): Promise<void> => {
      fireEvent.keyDown(input, { key: "Enter" })
      fireEvent.click(screen.getByRole("button", { name: "Save" }))
    })

    expect(rename).not.toHaveBeenCalled()
    // Still in edit mode with the draft intact.
    expect(input.isConnected).toBe(true)
    expect(input.value).toBe("   ")
  })

  it.each([
    [
      "Escape",
      (input: HTMLInputElement): void => {
        fireEvent.keyDown(input, { key: "Escape" })
      },
    ],
    [
      "Cancel",
      (): void => {
        fireEvent.click(screen.getByRole("button", { name: "Cancel" }))
      },
    ],
  ])("discards the rename on %s", async (_label: string, leave: (
    input: HTMLInputElement,
  ) => void): Promise<void> => {
    const rename = vi.spyOn(api, "renameFolder")
    setup()
    const input: HTMLInputElement = startRenameBeta()

    fireEvent.change(input, { target: { value: "Discarded" } })
    await act(async (): Promise<void> => leave(input))

    expect(rename).not.toHaveBeenCalled()
    expect(screen.queryByDisplayValue("Discarded")).toBeNull()
    expect(screen.getByText("Beta")).toBeInTheDocument()
  })
})

describe("FolderManager — delete", (): void => {
  const MESSAGE: string =
    'Delete folder "Beta"? Feeds will be moved to uncategorized.'

  it("does nothing when the confirmation is declined", async (): Promise<void> => {
    const confirm = vi.fn((): boolean => false)
    vi.stubGlobal("confirm", confirm)
    const remove = vi.spyOn(api, "deleteFolder")
    setup()

    await act(async (): Promise<void> => {
      fireEvent.click(
        within(rowOf("Beta")).getByRole("button", { name: "Delete" }),
      )
    })

    expect(confirm).toHaveBeenCalledWith(MESSAGE)
    expect(remove).not.toHaveBeenCalled()
  })

  it("deletes the folder and refetches once confirmed", async (): Promise<void> => {
    const confirm = vi.fn((): boolean => true)
    vi.stubGlobal("confirm", confirm)
    const remove = vi.spyOn(api, "deleteFolder").mockResolvedValue(undefined)
    setup()
    const invalidate = vi.spyOn(queryClient, "invalidateQueries")

    fireEvent.click(
      within(rowOf("Beta")).getByRole("button", { name: "Delete" }),
    )

    await waitFor((): void => expectRefetched(invalidate))
    expect(confirm).toHaveBeenCalledWith(MESSAGE)
    // mutationFn is api.deleteFolder itself, so TanStack appends a context
    // argument: compare only the first one.
    expect(remove.mock.calls[0][0]).toBe(2)
  })
})

describe("FolderManager — errors", (): void => {
  it.each([
    [
      "create",
      (): void => {
        const input: HTMLInputElement = newNameInput()
        fireEvent.change(input, { target: { value: "News" } })
        fireEvent.submit(input.closest("form") as HTMLFormElement)
      },
    ],
    [
      "rename",
      (): void => {
        const input: HTMLInputElement = startRenameBeta()
        fireEvent.change(input, { target: { value: "B2" } })
        fireEvent.keyDown(input, { key: "Enter" })
      },
    ],
    [
      "delete",
      (): void => {
        fireEvent.click(
          within(rowOf("Beta")).getByRole("button", { name: "Delete" }),
        )
      },
    ],
  ])("reports a failed %s", async (label: string, trigger: () => void): Promise<void> => {
    const failure: Error = new Error("nope")
    // Spy before rendering: deleteFolder's mutationFn is api.deleteFolder
    // itself, captured when the component renders.
    vi.spyOn(api, "createFolder").mockRejectedValue(failure)
    vi.spyOn(api, "renameFolder").mockRejectedValue(failure)
    vi.spyOn(api, "deleteFolder").mockRejectedValue(failure)
    vi.stubGlobal(
      "confirm",
      vi.fn((): boolean => true),
    )
    setup()

    trigger()

    await waitFor((): void => expect(onError).toHaveBeenCalled())
    // onError is handed to useMutation directly, so TanStack passes extra
    // arguments after the error.
    expect(vi.mocked(onError).mock.calls[0][0]).toBe(failure)
    if (label === "rename") {
      expect(screen.getByDisplayValue("B2")).toBeInTheDocument()
    }
  })
})
