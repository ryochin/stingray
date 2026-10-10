import { useQuery } from "@tanstack/react-query"
import type {
  Article,
  Feed,
  FeedStats,
  Folder,
  RefreshStatus,
} from "../api/client"
import { api } from "../api/client"
import { type TimeRangeId, timeRangeDays } from "../utils/articleView"

interface UseArticleQueriesInput {
  showUnreadOnly: boolean
  timeRangeId: TimeRangeId
}

interface UseArticleQueriesResult {
  status: RefreshStatus | undefined
  running: boolean
  activeInterval: number
  allArticles: Article[] | undefined
  articlesLoading: boolean
  articlesError: boolean
  feeds: Feed[] | undefined
  folders: Folder[] | undefined
  feedStats: Record<string, FeedStats> | undefined
}

// How long a read article keeps coming back in unread mode. A tab left open
// longer than this loses the articles read before then on the next poll, as
// does a read article pushed out of the response limit by newer reads.
export const UNREAD_MODE_READ_WITHIN_HOURS: number = 24

type ArticleQueryOpts = NonNullable<Parameters<typeof api.getArticles>[0]>

/** Owns the read-only data fetches that back the Articles view. Centralises
 *  the polling cadence (`activeInterval`) and the `sinceDays` derivation so
 *  the route component doesn't have to thread those through manually. */
export function useArticleQueries({
  showUnreadOnly,
  timeRangeId,
}: UseArticleQueriesInput): UseArticleQueriesResult {
  // Subscribe to status so refetchInterval reacts immediately when a refresh
  // starts/ends, instead of waiting for the next scheduled tick. Header also
  // owns a status observer — both share the same cache key.
  const { data: status } = useQuery({
    queryKey: ["status"],
    queryFn: api.getStatus,
    refetchInterval: (query) => (query.state.data?.running ? 2_000 : 30_000),
  })
  const running: boolean = status?.running ?? false

  // While a refresh is running, poll faster so per-feed unread counts reflect
  // as each feed finishes (OPML import, manual refresh).
  const activeInterval: number = running ? 3_000 : 15_000

  // Unread mode bypasses the time filter: the user wants every still-unread
  // item to be reachable regardless of age. Otherwise the backend trims to
  // the selected window so the response naturally matches the visible list.
  const sinceDays: number | null = showUnreadOnly
    ? null
    : timeRangeDays(timeRangeId)

  // Unread mode asks the backend for unread rows, so an old unread item can't
  // fall outside the response limit. Recent reads ride along so what the
  // reader just finished stays listed (`sessionReadUrls` keeps it visible);
  // without them it would vanish on the refetch after each read flush.
  const articleOpts: ArticleQueryOpts = showUnreadOnly
    ? {
        sinceDays,
        unread: true,
        readWithinHours: UNREAD_MODE_READ_WITHIN_HOURS,
      }
    : { sinceDays }

  const {
    data: allArticles,
    isLoading: articlesLoading,
    isError: articlesError,
  } = useQuery({
    // `unread` is part of the key: unread mode and the All range share
    // `sinceDays: null` but get different responses.
    queryKey: ["articles", { sinceDays, unread: showUnreadOnly }],
    queryFn: (): ReturnType<typeof api.getArticles> =>
      api.getArticles(articleOpts),
    refetchInterval: activeInterval,
  })

  const { data: feeds } = useQuery({
    queryKey: ["feeds"],
    queryFn: api.getFeeds,
    refetchInterval: activeInterval,
  })

  const { data: folders } = useQuery({
    queryKey: ["folders"],
    queryFn: api.getFolders,
  })

  // Per-feed unread totals from the DB (not from the time-bounded /articles
  // response). This is the source of truth for sidebar badges so they stay
  // consistent regardless of the active time range.
  const { data: feedStats } = useQuery({
    queryKey: ["feed-stats"],
    queryFn: api.getFeedStats,
    refetchInterval: activeInterval,
  })

  return {
    status,
    running,
    activeInterval,
    allArticles,
    articlesLoading,
    articlesError,
    feeds,
    folders,
    feedStats,
  }
}
