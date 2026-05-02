import { useEffect, useRef } from "react"
import { useQueryClient } from "@tanstack/react-query"

// Watch the global refresh status and invalidate cache views on the
// running↔idle transitions so every page sees the final state without
// waiting for the next scheduled poll tick. Mounted from any page that has
// the latest `running` flag — the shared QueryClient cache means a single
// caller per route is enough.
export function useRefreshSync(running: boolean | undefined) {
  const queryClient = useQueryClient()
  const prevRunning = useRef(false)
  useEffect(() => {
    const current = running ?? false
    if (prevRunning.current !== current) {
      queryClient.invalidateQueries({ queryKey: ["articles"] })
      queryClient.invalidateQueries({ queryKey: ["feeds"] })
      queryClient.invalidateQueries({ queryKey: ["folders"] })
      queryClient.invalidateQueries({ queryKey: ["feed-stats"] })
    }
    prevRunning.current = current
  }, [running, queryClient])
}
