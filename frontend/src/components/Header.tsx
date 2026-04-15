import { useEffect, useRef } from "react"
import { Link } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "../api/client"

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString("ja-JP", {
    timeZone: "Asia/Tokyo",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  })
}

export default function Header() {
  const queryClient = useQueryClient()
  const { data: status } = useQuery({
    queryKey: ["status"],
    queryFn: api.getStatus,
    refetchInterval: (query) =>
      query.state.data?.running ? 2000 : false,
  })

  const prevRunning = useRef(false)
  useEffect(() => {
    if (prevRunning.current && !status?.running) {
      queryClient.invalidateQueries({ queryKey: ["articles"] })
    }
    prevRunning.current = status?.running ?? false
  }, [status?.running, queryClient])

  const refresh = useMutation({
    mutationFn: api.refresh,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["status"] })
    },
  })

  return (
    <header className="flex items-center justify-between px-6 py-3 border-b border-border bg-bg-secondary shrink-0">
      <div className="flex items-center gap-6">
        <Link to="/" className="text-xl font-semibold text-text-heading no-underline">
          News Reader
        </Link>
        <nav className="flex gap-4 text-sm">
          <Link to="/" className="text-text-muted hover:text-text no-underline">Articles</Link>
          <Link to="/feeds" className="text-text-muted hover:text-text no-underline">Feeds</Link>
          <Link to="/filters" className="text-text-muted hover:text-text no-underline">Filters</Link>
        </nav>
      </div>
      <div className="flex items-center gap-3 text-sm">
        {status?.running && (
          <span className="text-accent animate-pulse">Fetching...</span>
        )}
        {status?.last_finished_at && !status.running && (
          <span className="text-text-dim">
            {formatTime(status.last_finished_at)}
            {status.last_new_count != null && ` / ${status.last_new_count} new`}
          </span>
        )}
        <button
          onClick={() => refresh.mutate()}
          disabled={refresh.isPending || status?.running}
          className="flex items-center gap-1.5 px-3 py-1 rounded bg-bg-card text-text-muted hover:bg-accent hover:text-white disabled:opacity-40 transition-colors"
        >
          <svg className={`w-3.5 h-3.5 ${status?.running || refresh.isPending ? "animate-spin" : ""}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 2v6h-6" />
            <path d="M3 12a9 9 0 0 1 15-6.7L21 8" />
            <path d="M3 22v-6h6" />
            <path d="M21 12a9 9 0 0 1-15 6.7L3 16" />
          </svg>
          Refresh
        </button>
      </div>
    </header>
  )
}
