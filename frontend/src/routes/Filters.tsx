import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import type { JSX } from "react"
import { useRef, useState } from "react"
import type { FilterRule } from "../api/client"
import { api } from "../api/client"
import Header from "../components/Header"

export default function Filters(): JSX.Element {
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const [pattern, setPattern] = useState<string>("")
  const [target, setTarget] = useState<"title" | "both">("title")

  const { data: filters } = useQuery({
    queryKey: ["filters"],
    queryFn: api.getFilters,
  })

  // Sidebar badges and per-feed counts depend on filter state too, since
  // get_feed_stats hides filtered articles. Invalidate feed-stats here so
  // the sidebar reflects filter changes without waiting for the next poll.
  const invalidate = (): void => {
    queryClient.invalidateQueries({ queryKey: ["filters"] })
    queryClient.invalidateQueries({ queryKey: ["articles"] })
    queryClient.invalidateQueries({ queryKey: ["feed-stats"] })
  }

  const handleExport = async (): Promise<void> => {
    try {
      await api.exportFilters()
    } catch (err) {
      setError(err instanceof Error ? err.message : "Export failed")
    }
  }

  const createFilter = useMutation({
    mutationFn: ({ pattern, target }: { pattern: string; target: string }) =>
      api.createFilter(pattern, target),
    onSuccess: (): void => {
      invalidate()
      setPattern("")
    },
    onError: (err: Error): void => setError(err.message),
  })
  const deleteFilter = useMutation({
    mutationFn: api.deleteFilter,
    onSuccess: invalidate,
    onError: (err: Error): void => setError(err.message),
  })

  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleCreate = (event: React.FormEvent): void => {
    event.preventDefault()
    if (!pattern.trim()) return
    createFilter.mutate({ pattern: pattern.trim(), target })
  }

  const handleImport = async (
    event: React.ChangeEvent<HTMLInputElement>,
  ): Promise<void> => {
    const file: File | undefined = event.target.files?.[0]
    if (!file) return
    try {
      const result = await api.importFilters(file)
      invalidate()
      setError(null)
      alert(`Imported ${result.created} filters (${result.skipped} skipped)`)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Import failed")
    }
    if (fileInputRef.current) fileInputRef.current.value = ""
  }

  const isRegex = (pattern: string): boolean =>
    pattern.length > 2 && pattern.startsWith("/") && pattern.endsWith("/")

  return (
    <div className="flex flex-col h-screen">
      <Header />
      <main className="flex-1 overflow-y-auto px-7 py-5 max-w-6xl">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-text-heading">Filters</h2>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleExport}
              disabled={!filters?.length}
              className="px-3 py-1.5 rounded text-sm bg-bg-card text-text-muted border border-border hover:text-text transition-colors disabled:opacity-40"
            >
              Export JSON
            </button>
            <label className="px-3 py-1.5 rounded text-sm bg-bg-card text-text-muted border border-border hover:text-text transition-colors cursor-pointer">
              Import JSON
              <input
                ref={fileInputRef}
                type="file"
                accept=".json"
                onChange={handleImport}
                className="hidden"
              />
            </label>
          </div>
        </div>

        {error && <div className="text-red-400 text-sm mb-4">{error}</div>}

        <h3 className="text-sm font-semibold text-text-heading mb-3">
          Filter Rules
        </h3>
        <form onSubmit={handleCreate} className="flex gap-2 mb-3">
          <input
            value={pattern}
            onChange={(event: React.ChangeEvent<HTMLInputElement>): void =>
              setPattern(event.target.value)
            }
            className="bg-bg-card text-text border border-border rounded px-2 py-1.5 text-sm flex-1"
            placeholder="keyword or /regex/"
          />
          <select
            value={target}
            onChange={(event: React.ChangeEvent<HTMLSelectElement>): void =>
              setTarget(event.target.value as "title" | "both")
            }
            className="bg-bg-card text-text border border-border rounded px-2 py-1.5 text-sm"
          >
            <option value="title">Title</option>
            <option value="both">Both</option>
          </select>
          <button
            type="submit"
            disabled={createFilter.isPending || !pattern.trim()}
            className="px-3 py-1.5 rounded bg-accent text-white text-sm hover:opacity-90 transition-opacity disabled:opacity-40"
          >
            Add
          </button>
        </form>
        {(filters ?? []).length > 0 && (
          <div className="space-y-1">
            {(filters ?? []).map((filter: FilterRule) => (
              <div
                key={filter.id}
                className="flex items-center gap-2 p-2 bg-bg-secondary rounded border border-border"
              >
                <span
                  className={`flex-1 text-sm ${isRegex(filter.pattern) ? "font-mono text-amber-400" : "text-text"}`}
                >
                  {filter.pattern}
                </span>
                <span className="text-xs text-text-dim px-1.5 py-0.5 bg-bg-card rounded">
                  {filter.target}
                </span>
                <button
                  type="button"
                  onClick={(): void => deleteFilter.mutate(filter.id)}
                  className="px-2 py-1 rounded text-xs bg-bg-card text-red-400 hover:bg-red-900 hover:text-red-200 transition-colors"
                >
                  Delete
                </button>
              </div>
            ))}
          </div>
        )}
      </main>
    </div>
  )
}
