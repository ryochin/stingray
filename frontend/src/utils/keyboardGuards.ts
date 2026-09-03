/**
 * Shared entry conditions for the window-level keyboard shortcut handlers
 * (`useArticleKeyboard`, `useTabShortcuts`). Both listen on `window`, so every
 * keystroke in the app reaches them and each one has to decide, identically,
 * whether the key is really theirs to act on.
 */

/**
 * True when the keystroke belongs to a text-entry context, and a global
 * shortcut must therefore stay out of it.
 *
 * Deliberately does NOT test `defaultPrevented`: a card handled its own Enter
 * (ArticleCard calls preventDefault to focus itself) and still expects the
 * global "open in a new tab" binding to run afterwards. Callers that do want
 * to defer to an earlier handler must check it themselves.
 */
export function isShortcutSuppressed(e: KeyboardEvent): boolean {
  if (e.metaKey || e.ctrlKey || e.altKey) return true
  // Mid-composition keystrokes belong to the input method.
  if (e.isComposing) return true
  const el = e.target as HTMLElement | null
  const tag: string = el?.tagName ?? ""
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true
  return el?.isContentEditable === true
}

/**
 * True while a modal dialog owns the keyboard. Callers must skip anything that
 * would act on the UI *behind* the modal — the ShortcutsHelp overlay handles
 * only Enter/Space/Escape itself, so every other key bubbles up to window, and
 * trying out a key read off the still-open cheatsheet would otherwise drive
 * the article list underneath it.
 *
 * Keys that target the modal itself (closing it) must be handled before this
 * check.
 */
export function isModalOpen(): boolean {
  return document.querySelector('[aria-modal="true"]') != null
}
