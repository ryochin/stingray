import type { JSX } from "react"
import { useRef, useState } from "react"
import { api } from "../api/client"

function OpmlButtons({
  onError,
  onImported,
  feedCount,
}: {
  onError: (e: Error) => void
  onImported: () => void
  feedCount: number
}): JSX.Element {
  const [importResult, setImportResult] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const handleExport = async (): Promise<void> => {
    try {
      const blob: Blob = await api.exportOpml()
      const url: string = URL.createObjectURL(blob)
      const anchor: HTMLAnchorElement = document.createElement("a")
      anchor.href = url
      // Local-time date stamp keeps "today's backup" matching the user's
      // calendar regardless of UTC offset.
      const now: Date = new Date()
      const stamp: string = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, "0")}${String(now.getDate()).padStart(2, "0")}`
      anchor.download = `stingray_subscriptions_${stamp}.opml`
      anchor.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      onError(e as Error)
    }
  }

  const handleImport = async (
    event: React.ChangeEvent<HTMLInputElement>,
  ): Promise<void> => {
    const file: File | undefined = event.target.files?.[0]
    if (!file) return
    try {
      const result = await api.importOpml(file)
      setImportResult(
        `Imported: ${result.folders_created} folders, ${result.feeds_created} feeds (${result.feeds_skipped} skipped)`,
      )
      onImported()
    } catch (err) {
      onError(err as Error)
    }
    if (fileRef.current) fileRef.current.value = ""
  }

  return (
    <div className="flex items-center gap-2">
      {importResult && (
        <span className="text-xs text-text-muted mr-2">{importResult}</span>
      )}
      <button
        type="button"
        onClick={handleExport}
        disabled={feedCount === 0}
        className="px-3 py-1.5 rounded text-sm bg-bg-card text-text-muted border border-border hover:text-text transition-colors disabled:opacity-40 disabled:pointer-events-none"
      >
        Export OPML
      </button>
      <label className="px-3 py-1.5 rounded text-sm bg-bg-card text-text-muted border border-border hover:text-text transition-colors cursor-pointer">
        Import OPML
        <input
          ref={fileRef}
          type="file"
          accept=".opml,.xml"
          onChange={handleImport}
          className="hidden"
        />
      </label>
    </div>
  )
}

export default OpmlButtons
