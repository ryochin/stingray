import { useQuery } from "@tanstack/react-query"
import { api } from "../api/client"
import type { Feed } from "../api/client"

interface Props {
  activeFeedId: number | null
  onSelect: (feedId: number | null) => void
  articleCounts: Map<number, number>
}

export default function Sidebar({ activeFeedId, onSelect, articleCounts }: Props) {
  const { data: feeds } = useQuery({
    queryKey: ["feeds"],
    queryFn: api.getFeeds,
  })

  const enabledFeeds = feeds?.filter((f: Feed) => f.enabled) ?? []
  const totalCount = Array.from(articleCounts.values()).reduce((a, b) => a + b, 0)

  return (
    <nav className="w-56 shrink-0 bg-bg-secondary border-r border-border overflow-y-auto py-2">
      <button
        onClick={() => onSelect(null)}
        className={`flex justify-between items-center w-full px-4 py-2.5 text-sm text-left transition-colors cursor-pointer ${
          activeFeedId === null
            ? "text-accent-text"
            : "text-text-muted hover:text-text"
        }`}
      >
        <span>All</span>
        <span className={`text-xs px-2 py-0.5 rounded-full ${
          activeFeedId === null ? "bg-accent-bg text-accent-text" : "bg-bg-card text-text-muted"
        }`}>
          {totalCount}
        </span>
      </button>
      {enabledFeeds.map((feed: Feed) => (
        <button
          key={feed.id}
          onClick={() => onSelect(feed.id)}
          className={`flex justify-between items-center w-full px-4 py-2.5 text-sm text-left transition-colors cursor-pointer ${
            activeFeedId === feed.id
              ? "text-accent-text"
              : "text-text-muted hover:text-text"
          }`}
        >
          <span className="truncate mr-2">{feed.name}</span>
          <span className={`text-xs px-2 py-0.5 rounded-full shrink-0 ${
            activeFeedId === feed.id ? "bg-accent-bg text-accent-text" : "bg-bg-card text-text-muted"
          }`}>
            {articleCounts.get(feed.id) ?? 0}
          </span>
        </button>
      ))}
    </nav>
  )
}
