import { useState, useRef } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { api } from "../api/client"
import type { FilterRule } from "../api/client"
import Header from "../components/Header"

export default function Filters() {
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const [pattern, setPattern] = useState("")
  const [target, setTarget] = useState<"title" | "both">("title")

  const { data: filters } = useQuery({
    queryKey: ["filters"],
    queryFn: api.getFilters,
  })

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["filters"] })
    queryClient.invalidateQueries({ queryKey: ["articles"] })
  }

  const createFilter = useMutation({
    mutationFn: ({ pattern, target }: { pattern: string, target: string }) =>
      api.createFilter(pattern, target),
    onSuccess: () => { invalidate(); setPattern("") },
    onError: (e: Error) => setError(e.message),
  })
  const deleteFilter = useMutation({
    mutationFn: api.deleteFilter,
    onSuccess: invalidate,
    onError: (e: Error) => setError(e.message),
  })

  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault()
    if (!pattern.trim()) return
    createFilter.mutate({ pattern: pattern.trim(), target })
  }

  const handleImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
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

  const isRegex = (p: string) => p.length > 2 && p.startsWith("/") && p.endsWith("/")

  return (
    <div className="flex flex-col h-screen">
      <Header />
      <main className="flex-1 overflow-y-auto px-7 py-5 max-w-6xl">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-text-heading">Filters</h2>
          <div className="flex items-center gap-2">
            <button
              onClick={() => api.exportFilters()}
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

        {error && (
          <div className="text-red-400 text-sm mb-4">{error}</div>
        )}

        <h3 className="text-sm font-semibold text-text-heading mb-3">Filter Rules</h3>
        <form onSubmit={handleCreate} className="flex gap-2 mb-3">
          <input
            value={pattern}
            onChange={(e) => setPattern(e.target.value)}
            className="bg-bg-card text-text border border-border rounded px-2 py-1.5 text-sm flex-1"
            placeholder="keyword or /regex/"
          />
          <select
            value={target}
            onChange={(e) => setTarget(e.target.value as "title" | "both")}
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
              <div key={filter.id} className="flex items-center gap-2 p-2 bg-bg-secondary rounded border border-border">
                <span className={`flex-1 text-sm ${isRegex(filter.pattern) ? "font-mono text-amber-400" : "text-text"}`}>
                  {filter.pattern}
                </span>
                <span className="text-xs text-text-dim px-1.5 py-0.5 bg-bg-card rounded">
                  {filter.target}
                </span>
                <button
                  onClick={() => deleteFilter.mutate(filter.id)}
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
