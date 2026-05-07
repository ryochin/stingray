import type { JSX } from "react"
import { useEffect, useRef, useState } from "react"

interface Props {
  disabled?: boolean
  onChoose: (olderThanHours: number | null) => void
  onChooseUnread: () => void
}

type Option = { label: string; hours: number | null }

const OPTIONS: readonly Option[] = [
  { label: "All", hours: null },
  { label: "Older than 12 hours", hours: 12 },
  { label: "Older than 48 hours", hours: 48 },
  { label: "Older than 1 week", hours: 24 * 7 },
  { label: "Older than 1 month", hours: 24 * 30 },
]

/** "Mark all as read" split into ALL / age-based cutoffs via a dropdown. */
export default function MarkAllReadMenu({
  disabled,
  onChoose,
  onChooseUnread,
}: Props): JSX.Element {
  const [open, setOpen] = useState<boolean>(false)
  const wrapRef = useRef<HTMLDivElement>(null)

  useEffect((): (() => void) | undefined => {
    if (!open) return
    const handleClick = (event: MouseEvent): void => {
      if (!wrapRef.current?.contains(event.target as Node)) setOpen(false)
    }
    const handleKey = (event: KeyboardEvent): void => {
      if (event.key === "Escape") setOpen(false)
    }
    window.addEventListener("mousedown", handleClick)
    window.addEventListener("keydown", handleKey)
    return (): void => {
      window.removeEventListener("mousedown", handleClick)
      window.removeEventListener("keydown", handleKey)
    }
  }, [open])

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        onClick={(): void => setOpen((value: boolean): boolean => !value)}
        disabled={disabled}
        className="text-sm px-3 py-1 rounded bg-bg-card text-text-muted hover:text-text transition-colors disabled:opacity-40"
      >
        Mark all as read ▾
      </button>
      {open && (
        <div className="absolute right-0 mt-1 z-20 min-w-[12rem] bg-bg-secondary border border-border rounded shadow-lg py-1">
          {OPTIONS.map(
            (opt: Option): JSX.Element => (
              <button
                key={opt.label}
                type="button"
                onClick={() => {
                  setOpen(false)
                  onChoose(opt.hours)
                }}
                className="block w-full text-left px-3 py-1.5 text-sm text-text hover:bg-bg-hover"
              >
                {opt.label}
              </button>
            ),
          )}
          <div className="my-1 border-t border-border" />
          <button
            type="button"
            onClick={() => {
              setOpen(false)
              onChooseUnread()
            }}
            className="block w-full text-left px-3 py-1.5 text-sm text-text-muted hover:bg-bg-hover hover:text-text"
          >
            Mark all as unread
          </button>
        </div>
      )}
    </div>
  )
}
