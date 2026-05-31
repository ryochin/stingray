import type { JSX, KeyboardEvent, MouseEvent } from "react"
import { useEffect, useRef } from "react"

interface Props {
  onClose: () => void
}

const SHORTCUTS: readonly (readonly [string, string])[] = [
  ["j", "Next article"],
  ["k", "Previous article"],
  ["Space", "Jump to next unread feed"],
  ["v / o / Enter", "Open in new tab"],
  ["m", "Toggle read/unread"],
  ["Shift+A", "Mark all as read"],
  ["u", "Toggle Unread / All"],
  ["?", "Show/hide this help"],
  ["a", "Go to Articles"],
  ["f", "Go to Feeds"],
]

/** Keyboard shortcut cheatsheet shown when the user presses `?`. */
export default function ShortcutsHelp({ onClose }: Props): JSX.Element {
  const overlayRef = useRef<HTMLDivElement>(null)

  // Pull focus into the overlay while it's open so its own keydown handler
  // owns Space/Enter/Escape. Otherwise focus stays on the scroll container
  // (<main>) and a Space behind the dialog would natively scroll the list
  // or trigger the feed-jump shortcut. Restore the previously focused
  // element on close so Space-scroll keeps working where the user left off.
  useEffect((): (() => void) => {
    const prevActive: HTMLElement | null =
      document.activeElement as HTMLElement | null
    overlayRef.current?.focus()
    return (): void => {
      prevActive?.focus({ preventScroll: true })
    }
  }, [])

  const handleOverlayKey = (event: KeyboardEvent<HTMLDivElement>): void => {
    if (event.key === "Enter" || event.key === " " || event.key === "Escape") {
      event.preventDefault()
      onClose()
    }
  }

  const handleOverlayClick = (event: MouseEvent<HTMLDivElement>): void => {
    if (event.target === event.currentTarget) onClose()
  }

  return (
    // biome-ignore lint/a11y/useSemanticElements: cannot use <button> because the overlay wraps a dialog with table/structured content (HTML5 disallows interactive content nesting)
    <div
      ref={overlayRef}
      className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 outline-none"
      role="button"
      tabIndex={0}
      aria-label="Close keyboard shortcuts help"
      onClick={handleOverlayClick}
      onKeyDown={handleOverlayKey}
    >
      <div
        className="bg-bg-secondary border border-border rounded-lg p-6 max-w-sm"
        role="dialog"
        aria-modal="true"
        aria-labelledby="shortcuts-help-title"
      >
        <h3
          id="shortcuts-help-title"
          className="text-text-heading font-semibold mb-4"
        >
          Keyboard Shortcuts
        </h3>
        <table className="text-sm w-full">
          <tbody>
            {SHORTCUTS.map(
              ([key, desc]: readonly [string, string]): JSX.Element => (
                <tr key={key}>
                  <td className="pr-4 py-1">
                    <kbd className="px-1.5 py-0.5 rounded bg-bg-card text-accent-text text-xs font-mono">
                      {key}
                    </kbd>
                  </td>
                  <td className="py-1 text-text-muted">{desc}</td>
                </tr>
              ),
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
