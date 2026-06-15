import type { Query, QueryClient } from "@tanstack/react-query"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import type { JSX } from "react"
import { Link } from "react-router-dom"
import type { RefreshStatus } from "../api/client"
import { api } from "../api/client"
import { useRefreshSync } from "../hooks/useRefreshSync"
import { formatTime } from "../utils/date"

export default function Header(): JSX.Element {
  const queryClient: QueryClient = useQueryClient()
  const { data: status } = useQuery({
    queryKey: ["status"],
    queryFn: api.getStatus,
    refetchInterval: (query: Query<RefreshStatus>): number =>
      query.state.data?.running ? 2000 : 30_000,
    refetchOnWindowFocus: true,
  })

  useRefreshSync(status?.running)

  const refresh = useMutation({
    mutationFn: api.refresh,
    onSuccess: (): void => {
      queryClient.invalidateQueries({ queryKey: ["status"] })
    },
  })

  return (
    <header className="flex items-center justify-between px-6 py-3 border-b border-border bg-bg-secondary shrink-0">
      <div className="flex items-center gap-6 -ml-2">
        <Link
          to="/"
          className="flex items-center gap-2 font-display text-xl font-[250] tracking-wide text-text-heading no-underline"
        >
          {/* logo.png 120×80 (1x) / logo@2x.png 240×160 (2x); rendered at the
              heading font-size via h-5, width auto-keeps the 3:2 aspect ratio. */}
          <img
            src="/logo.png"
            srcSet="/logo.png 1x, /logo@2x.png 2x"
            width={120}
            height={80}
            alt=""
            className="h-5 w-auto shrink-0"
          />
          Stingray
        </Link>
        <nav className="relative top-0.5 flex gap-4 text-sm">
          <Link to="/" className="text-text-muted hover:text-text no-underline">
            Articles
          </Link>
          <Link
            to="/feeds"
            className="text-text-muted hover:text-text no-underline"
          >
            Feeds
          </Link>
          <Link
            to="/filters"
            className="text-text-muted hover:text-text no-underline"
          >
            Filters
          </Link>
        </nav>
      </div>
      <div className="flex items-center gap-3 text-sm">
        {status &&
          (status.llm_enabled === false ? (
            <span
              className="text-text-dim"
              title="LLM is disabled in config.yml (ollama.enabled = false)"
            >
              – LLM off
            </span>
          ) : status.llm_available ? (
            <span
              className="flex items-center gap-1 text-text-muted"
              title="LLM online"
            >
              <span
                className="inline-block w-2 h-2 rounded-full bg-green-500"
                aria-hidden
              />
              LLM
            </span>
          ) : (
            <span
              className="text-amber-400"
              title={
                status.llm_error
                  ? `LLM unavailable: ${status.llm_error}`
                  : "LLM unavailable"
              }
            >
              ⚠ LLM offline
            </span>
          ))}
        {status?.running && (
          <span className="text-accent animate-pulse">Fetching...</span>
        )}
        {status?.last_finished_at && !status.running && (
          <span className="text-text-muted">
            {formatTime(status.last_finished_at)}
            {status.last_new_count != null && ` / ${status.last_new_count} new`}
          </span>
        )}
        <button
          type="button"
          onClick={() => refresh.mutate()}
          disabled={refresh.isPending || status?.running}
          className="flex items-center gap-1.5 px-3 py-1 rounded bg-bg-card text-text-muted hover:bg-accent hover:text-white disabled:opacity-40 transition-colors"
        >
          <svg
            className={`w-3.5 h-3.5 ${status?.running || refresh.isPending ? "animate-spin" : ""}`}
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <title>Refresh</title>
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
