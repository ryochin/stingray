import { type JSX, useEffect, useRef } from "react"

interface Props {
  label: string
  subLabel?: string
  className?: string
  /** Increment to replay the pulse animation in place. Animations are run
   *  via the Web Animations API on the inner group so the outer wrapper —
   *  the element the virtualizer measures — never unmounts or changes
   *  bounding-rect, eliminating the scroll oscillation that key-based
   *  remounts caused. */
  pulseKey?: number
}

/** Sparkle icon + label, used by both the end-of-list sentinel and the
 *  empty-state message when no unread items remain. An optional `subLabel`
 *  renders on the line directly under `label` and fades in. */
export default function CaughtUpIndicator({
  label,
  subLabel,
  className,
  pulseKey,
}: Props): JSX.Element {
  const innerRef = useRef<HTMLDivElement>(null)
  // Hold the in-flight Animation so rapid j-at-end presses cancel the prior
  // pulse before starting a new one (and so unmount cancels the last one).
  // Without this, overlapping animations would race the transform on the
  // same element non-deterministically on hot keyboards.
  const pulseAnimRef = useRef<Animation | null>(null)
  useEffect((): (() => void) | undefined => {
    // Skip the initial 0 — the pulse should only play on user-driven
    // increments (j-at-end), not on first paint.
    if (pulseKey == null || pulseKey === 0) return
    const el: HTMLDivElement | null = innerRef.current
    if (!el) return
    pulseAnimRef.current?.cancel()
    pulseAnimRef.current = el.animate(
      [
        { transform: "scale(1)" },
        { transform: "scale(1.15)", offset: 0.4 },
        { transform: "scale(1)" },
      ],
      { duration: 150, easing: "ease-out" },
    )
    return (): void => {
      pulseAnimRef.current?.cancel()
      pulseAnimRef.current = null
    }
  }, [pulseKey])

  return (
    <div className="flex flex-col items-center gap-1.5 py-10">
      <div
        ref={innerRef}
        className={`flex flex-col items-center gap-2 origin-center ${className ?? "text-text-dim/60"}`}
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
      <span
        // Remount on string change so the fade animation re-runs whenever
        // the hint text appears or switches variant. The outer wrapper is
        // unaffected because `min-h-[1rem]` reserves the line either way.
        key={subLabel ?? "off"}
        className={`text-xs text-text-dim min-h-[1rem] whitespace-nowrap ${subLabel ? "animate-caught-up-hint-fade" : ""}`}
      >
        {subLabel ?? " "}
      </span>
    </div>
  )
}
