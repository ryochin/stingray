import type { JSX } from "react"
import { useState } from "react"
import type { FeedCandidate, FeedCreate, Folder } from "../api/client"

interface Props {
  // Resolves on a successful add and rejects on failure (including the 422
  // "feed candidates" path), so the form can clear its inputs only on success.
  onAdd: (f: FeedCreate) => Promise<unknown>
  isAdding: boolean
  folders: Folder[]
  candidates: FeedCandidate[] | null
  candidatesFor: string | null
  // Resolves on a successful add of the picked candidate; same contract as
  // `onAdd` so the form clears its inputs once the candidate is added.
  onPickCandidate: (href: string) => Promise<unknown>
  onDismissCandidates: () => void
}

function AddFeedForm({
  onAdd,
  isAdding,
  folders,
  candidates,
  candidatesFor,
  onPickCandidate,
  onDismissCandidates,
}: Props): JSX.Element {
  const [name, setName] = useState<string>("")
  const [url, setUrl] = useState<string>("")
  const [folderId, setFolderId] = useState<number | undefined>(undefined)

  // Clear the per-feed inputs after a successful add, but only the fields the
  // user has not edited since submitting: a slow add must not wipe a value the
  // user already started typing for the next feed. The folder is intentionally
  // kept so several feeds can be added to it in a row.
  const clearInputsIfUnchanged = (
    submittedName: string,
    submittedUrl: string,
  ): void => {
    setName((cur: string): string => (cur === submittedName ? "" : cur))
    setUrl((cur: string): string => (cur === submittedUrl ? "" : cur))
  }

  const submit = (event: React.FormEvent): void => {
    event.preventDefault()
    const body: FeedCreate = { name, url }
    if (folderId != null) body.folder_id = folderId
    onAdd(body)
      .then((): void => clearInputsIfUnchanged(name, url))
      .catch((): void => {
        // Keep the inputs on failure or when the URL resolved to feed
        // candidates, so the user can fix the value or pick a candidate. The
        // parent surfaces the error / candidate list.
      })
  }

  const reset = (): void => {
    setName("")
    setUrl("")
    setFolderId(undefined)
    onDismissCandidates()
  }

  const pick = (href: string): void => {
    setUrl(href)
    // The candidate is added with an auto-detected name (name: ""), so clear
    // against that on success — same untouched-only guard as the main submit.
    onPickCandidate(href)
      .then((): void => clearInputsIfUnchanged("", href))
      .catch((): void => {})
  }

  return (
    <div className="mb-6">
      <form
        onSubmit={submit}
        onReset={reset}
        className="flex flex-wrap gap-3 items-end p-4 bg-bg-secondary rounded-lg border border-border"
      >
        <label className="flex flex-col gap-1 flex-1 min-w-48">
          <span className="text-xs text-text-muted">URL</span>
          <div className="relative">
            <svg
              className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <line x1="12" y1="5" x2="12" y2="19" />
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
            <input
              value={url}
              onChange={(event: React.ChangeEvent<HTMLInputElement>): void =>
                setUrl(event.target.value)
              }
              required
              className="w-full bg-bg-card text-text border border-border rounded pl-8 pr-2 py-1.5 text-sm"
              placeholder="https://example.com/feed"
            />
          </div>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs text-text-muted">Name</span>
          <input
            value={name}
            onChange={(event: React.ChangeEvent<HTMLInputElement>): void =>
              setName(event.target.value)
            }
            className="bg-bg-card text-text border border-border rounded px-2 py-1.5 text-sm w-40"
            placeholder="Auto-detect"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs text-text-muted">Folder</span>
          <select
            value={folderId ?? ""}
            onChange={(event: React.ChangeEvent<HTMLSelectElement>): void =>
              setFolderId(
                event.target.value ? Number(event.target.value) : undefined,
              )
            }
            className="bg-bg-card text-text border border-border rounded px-2 py-1.5 text-sm"
          >
            <option value="">--</option>
            {folders.map((folder: Folder) => (
              <option key={folder.id} value={folder.id}>
                {folder.name}
              </option>
            ))}
          </select>
        </label>
        <button
          type="submit"
          disabled={isAdding}
          className="px-4 py-1.5 rounded bg-accent text-white text-sm hover:opacity-90 transition-opacity disabled:opacity-40"
        >
          {isAdding ? "Adding..." : "Add"}
        </button>
      </form>
      {candidates && candidates.length > 0 && (
        <div className="mt-2 p-3 bg-bg-card border border-border rounded-lg">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm text-text-muted">
              {candidatesFor
                ? `"${candidatesFor}" is an HTML page. Pick a feed:`
                : "That URL is an HTML page. Pick a feed:"}
            </span>
            <button
              type="button"
              onClick={onDismissCandidates}
              className="text-xs text-text-dim hover:text-text"
            >
              Dismiss
            </button>
          </div>
          <ul className="space-y-1">
            {candidates.map((candidate: FeedCandidate) => (
              <li key={candidate.href} className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={(): void => pick(candidate.href)}
                  className="text-xs px-2 py-1 rounded bg-accent text-white hover:opacity-90"
                >
                  Use
                </button>
                <div className="flex flex-col text-sm min-w-0">
                  {candidate.title && (
                    <span className="text-text-heading truncate">
                      {candidate.title}
                    </span>
                  )}
                  <span className="text-text-dim text-xs truncate">
                    {candidate.type
                      ? `[${candidate.type.replace("application/", "")}] `
                      : ""}
                    {candidate.href}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

export default AddFeedForm
