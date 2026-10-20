/**
 * OpmlButtons: export downloads the OPML under a local-date file name; import
 * uploads the chosen file, reports the counts, and clears the file input so
 * the same file can be picked again.
 */

import { act, fireEvent, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { api } from "../api/client"
import OpmlButtons from "./OpmlButtons"

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>()
  return {
    ...actual,
    api: { exportOpml: vi.fn(), importOpml: vi.fn() },
  }
})

type ImportResult = Awaited<ReturnType<typeof api.importOpml>>

const RESULT: ImportResult = {
  folders_created: 2,
  feeds_created: 5,
  feeds_skipped: 1,
}

function renderButtons(feedCount: number = 3) {
  const onError = vi.fn()
  const onImported = vi.fn()
  render(
    <OpmlButtons
      onError={onError}
      onImported={onImported}
      feedCount={feedCount}
    />,
  )
  return { onError, onImported }
}

function fileInput(): HTMLInputElement {
  const input: Element | null = document.querySelector('input[type="file"]')
  if (!(input instanceof HTMLInputElement)) throw new Error("no file input")
  return input
}

afterEach((): void => {
  vi.restoreAllMocks()
  vi.mocked(api.exportOpml).mockReset()
  vi.mocked(api.importOpml).mockReset()
})

describe("OpmlButtons export", (): void => {
  const originalTz: string | undefined = process.env.TZ

  beforeEach((): void => {
    vi.useFakeTimers({ toFake: ["Date"] })
  })

  afterEach((): void => {
    vi.useRealTimers()
    process.env.TZ = originalTz
  })

  it("downloads the OPML under today's local date", async (): Promise<void> => {
    // 20:00 UTC is already the next day in Tokyo: the stamp must follow the
    // user's calendar, not UTC.
    process.env.TZ = "Asia/Tokyo"
    vi.setSystemTime(new Date("2026-10-20T20:00:00Z"))
    const blob: Blob = new Blob(["<opml/>"])
    vi.mocked(api.exportOpml).mockResolvedValue(blob)
    const createObjectURL = vi
      .spyOn(URL, "createObjectURL")
      .mockReturnValue("blob:opml")
    const revokeObjectURL = vi
      .spyOn(URL, "revokeObjectURL")
      .mockImplementation((): void => {})
    let clicked: HTMLAnchorElement | null = null
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ): void {
      clicked = this
    })
    const { onError } = renderButtons()

    await act(async (): Promise<void> => {
      fireEvent.click(screen.getByRole("button", { name: "Export OPML" }))
    })

    expect(createObjectURL).toHaveBeenCalledWith(blob)
    expect(clicked).not.toBeNull()
    const anchor: HTMLAnchorElement = clicked as unknown as HTMLAnchorElement
    expect(anchor.download).toBe("stingray_subscriptions_20261021.opml")
    expect(anchor.href).toBe("blob:opml")
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:opml")
    expect(onError).not.toHaveBeenCalled()
  })

  it("reports a failed export", async (): Promise<void> => {
    const error: Error = new Error("API error: 500")
    vi.mocked(api.exportOpml).mockRejectedValue(error)
    const { onError } = renderButtons()

    await act(async (): Promise<void> => {
      fireEvent.click(screen.getByRole("button", { name: "Export OPML" }))
    })

    expect(onError).toHaveBeenCalledExactlyOnceWith(error)
  })

  it("is disabled when there are no feeds", (): void => {
    renderButtons(0)
    expect(screen.getByRole("button", { name: "Export OPML" })).toBeDisabled()
  })
})

describe("OpmlButtons import", (): void => {
  const file: File = new File(["<opml/>"], "subs.opml", { type: "text/xml" })

  it("uploads the chosen file, reports counts, then clears the input", async (): Promise<void> => {
    let resolve: (value: ImportResult) => void = (): void => {}
    vi.mocked(api.importOpml).mockReturnValue(
      new Promise<ImportResult>((r): void => {
        resolve = r
      }),
    )
    const { onError, onImported } = renderButtons()

    await userEvent.upload(fileInput(), file)

    expect(api.importOpml).toHaveBeenCalledExactlyOnceWith(file)
    // Still pending: the input holds the selection until the upload settles.
    expect(fileInput().value).not.toBe("")

    await act(async (): Promise<void> => {
      resolve(RESULT)
    })

    expect(
      screen.getByText("Imported: 2 folders, 5 feeds (1 skipped)"),
    ).toBeInTheDocument()
    expect(onImported).toHaveBeenCalledTimes(1)
    expect(onError).not.toHaveBeenCalled()
    expect(fileInput().value).toBe("")
  })

  it("reports a failed import and still clears the input", async (): Promise<void> => {
    const error: Error = new Error("API error: 400")
    let reject: (reason: Error) => void = (): void => {}
    vi.mocked(api.importOpml).mockReturnValue(
      new Promise<ImportResult>((_r, j): void => {
        reject = j
      }),
    )
    const { onError, onImported } = renderButtons()

    await userEvent.upload(fileInput(), file)
    expect(fileInput().value).not.toBe("")
    await act(async (): Promise<void> => {
      reject(error)
    })

    expect(onError).toHaveBeenCalledExactlyOnceWith(error)
    expect(onImported).not.toHaveBeenCalled()
    expect(screen.queryByText(/^Imported:/)).toBeNull()
    expect(fileInput().value).toBe("")
  })

  it("does nothing when the selection is empty", (): void => {
    renderButtons()
    fireEvent.change(fileInput(), { target: { files: [] } })
    expect(api.importOpml).not.toHaveBeenCalled()
  })
})
