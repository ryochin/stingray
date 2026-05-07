import type { JSX } from "react"

interface Props {
  label: string
  className?: string
}

/** Sparkle icon + label, used by both the end-of-list sentinel and the
 *  empty-state message when no unread items remain. */
export default function CaughtUpIndicator({
  label,
  className,
}: Props): JSX.Element {
  return (
    <div
      className={`flex flex-col items-center gap-2 py-10 ${className ?? "text-text-dim/60"}`}
    >
      <svg
        className="w-7 h-7 text-accent-text/70"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <title>Caught up</title>
        <path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z" />
        <path d="M5 3v4" />
        <path d="M19 17v4" />
        <path d="M3 5h4" />
        <path d="M17 19h4" />
      </svg>
      <span className="text-sm">{label}</span>
    </div>
  )
}
