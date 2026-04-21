import { useEffect, useRef, useState } from "react"

interface Props {
  disabled?: boolean
  onChoose: (olderThanHours: number | null) => void
}

const OPTIONS: readonly { label: string, hours: number | null }[] = [
  { label: "All", hours: null },
  { label: "Older than 48 hours", hours: 48 },
  { label: "Older than 1 week", hours: 24 * 7 },
  { label: "Older than 1 month", hours: 24 * 30 },
]

/** "Mark all as read" split into ALL / age-based cutoffs via a dropdown. */
export default function MarkAllReadMenu({ disabled, onChoose }: Props) {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handleClick = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false)
    }
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false)
    }
    window.addEventListener("mousedown", handleClick)
    window.addEventListener("keydown", handleKey)
    return () => {
      window.removeEventListener("mousedown", handleClick)
      window.removeEventListener("keydown", handleKey)
    }
  }, [open])

  return (
    <div ref={wrapRef} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
        className="text-sm px-3 py-1 rounded bg-bg-card text-text-muted hover:text-text transition-colors disabled:opacity-40"
      >
        Mark all as read ▾
      </button>
      {open && (
        <div className="absolute right-0 mt-1 z-20 min-w-[12rem] bg-bg-secondary border border-border rounded shadow-lg py-1">
          {OPTIONS.map((opt) => (
            <button
              key={opt.label}
              onClick={() => {
                setOpen(false)
                onChoose(opt.hours)
              }}
              className="block w-full text-left px-3 py-1.5 text-sm text-text hover:bg-bg-hover"
            >
              {opt.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
