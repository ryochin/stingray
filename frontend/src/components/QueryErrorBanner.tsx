import type { JSX } from "react"

interface QueryErrorBannerProps {
  /** Human-readable names of the resources whose query is in error. */
  failed: string[]
  /** Spacing around the line; defaults to the gap above page content. */
  className?: string
}

/** One line naming every resource a screen failed to load. React Query keeps
 *  the last good data on a failed refetch, so this sits beside the content
 *  rather than replacing it, and clears itself on the next success. */
export default function QueryErrorBanner({
  failed,
  className = "mb-4",
}: QueryErrorBannerProps): JSX.Element | null {
  if (failed.length === 0) return null
  return (
    <div role="alert" className={`text-red-400 ${className}`}>
      Failed to load {failed.join(", ")}.
    </div>
  )
}
