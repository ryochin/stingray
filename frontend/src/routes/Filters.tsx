import { useState } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { api } from "../api/client"
import type { NgWord } from "../api/client"
import Header from "../components/Header"

export default function Filters() {
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const [pattern, setPattern] = useState("")
  const [target, setTarget] = useState("title")

  const { data: ngWords } = useQuery({
    queryKey: ["ng-words"],
    queryFn: api.getNgWords,
  })

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["ng-words"] })
    queryClient.invalidateQueries({ queryKey: ["articles"] })
  }

  const createNgWord = useMutation({
    mutationFn: ({ pattern, target }: { pattern: string, target: string }) =>
      api.createNgWord(pattern, target),
    onSuccess: () => { invalidate(); setPattern("") },
    onError: (e: Error) => setError(e.message),
  })
  const deleteNgWord = useMutation({
    mutationFn: api.deleteNgWord,
    onSuccess: invalidate,
    onError: (e: Error) => setError(e.message),
  })

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault()
    if (!pattern.trim()) return
    createNgWord.mutate({ pattern: pattern.trim(), target })
  }

  const isRegex = (p: string) => p.length > 2 && p.startsWith("/") && p.endsWith("/")

  return (
    <div className="flex flex-col h-screen">
      <Header />
      <main className="flex-1 overflow-y-auto px-7 py-5 max-w-6xl">
        <h2 className="text-lg font-semibold text-text-heading mb-4">Filters</h2>

        {error && (
          <div className="text-red-400 text-sm mb-4">{error}</div>
        )}

        <h3 className="text-sm font-semibold text-text-heading mb-3">NG Words</h3>
        <form onSubmit={handleCreate} className="flex gap-2 mb-3">
          <input
            value={pattern}
            onChange={(e) => setPattern(e.target.value)}
            className="bg-bg-card text-text border border-border rounded px-2 py-1.5 text-sm flex-1"
            placeholder="keyword or /regex/"
          />
          <select
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            className="bg-bg-card text-text border border-border rounded px-2 py-1.5 text-sm"
          >
            <option value="title">Title</option>
            <option value="both">Both</option>
          </select>
          <button
            type="submit"
            disabled={createNgWord.isPending || !pattern.trim()}
            className="px-3 py-1.5 rounded bg-accent text-white text-sm hover:opacity-90 transition-opacity disabled:opacity-40"
          >
            Add
          </button>
        </form>
        {(ngWords ?? []).length > 0 && (
          <div className="space-y-1">
            {(ngWords ?? []).map((ng: NgWord) => (
              <div key={ng.id} className="flex items-center gap-2 p-2 bg-bg-secondary rounded border border-border">
                <span className={`flex-1 text-sm ${isRegex(ng.pattern) ? "font-mono text-amber-400" : "text-text"}`}>
                  {ng.pattern}
                </span>
                <span className="text-xs text-text-dim px-1.5 py-0.5 bg-bg-card rounded">
                  {ng.target}
                </span>
                <button
                  onClick={() => deleteNgWord.mutate(ng.id)}
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
