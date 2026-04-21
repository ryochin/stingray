interface Props {
  onClose: () => void
}

const SHORTCUTS: readonly (readonly [string, string])[] = [
  ["j", "Next article"],
  ["k", "Previous article"],
  ["v / o / Enter", "Open in new tab"],
  ["m", "Toggle read/unread"],
  ["Shift+A", "Mark all as read"],
  ["?", "Show/hide this help"],
  ["a", "Go to Articles"],
  ["f", "Go to Feeds"],
]

/** Keyboard shortcut cheatsheet shown when the user presses `?`. */
export default function ShortcutsHelp({ onClose }: Props) {
  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-bg-secondary border border-border rounded-lg p-6 max-w-sm" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-text-heading font-semibold mb-4">Keyboard Shortcuts</h3>
        <table className="text-sm w-full">
          <tbody>
            {SHORTCUTS.map(([key, desc]) => (
              <tr key={key}>
                <td className="pr-4 py-1">
                  <kbd className="px-1.5 py-0.5 rounded bg-bg-card text-accent-text text-xs font-mono">{key}</kbd>
                </td>
                <td className="py-1 text-text-muted">{desc}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
